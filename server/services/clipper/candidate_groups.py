"""
ClipForge — AI Stream Clipper: moments, not variants, and who gets judged.

WHAT THIS FIXES. The judge saw `sorted(cands, -overall)[:80]`. Measured on the
four-hour audited source that was 80 of 909 — 9% of the field — and one story
candidate out of 38. After the chunk planner landed it became 80 of 957 and one
of 75, because raising recall makes the ceiling MORE binding, not less.

Two separate defects sit inside that one line:

  * it ranks VARIANTS. The story path proposes two to four cuts of one anchor,
    so a single moment can spend four of the eighty slots arguing with itself
    while a different moment gets none;
  * it ranks by the HEURISTIC score, which is the thing the judge exists to
    correct. A moment the heuristic cannot see is a moment the judge is never
    given the chance to find.

So the pool is built from MOMENTS, and it is built on a budget: a share for the
heuristic's best, a share for grounded story evidence, and the rest spent
greedily on the parts of the stream, the threads and the archetypes nothing has
covered yet.

WHAT IS PURE HERE. Everything. Candidates in, groups and a shortlist out.

HOW STABLE THE IDS ARE, precisely. Within a run, exactly. Across runs, only as
stable as the thing they are built from: a payoff time is quantised into a
bucket so ordinary model jitter of a second or two lands in the same id, but a
model that moves a payoff by ten seconds produces a different moment and should.
That is the honest limit, and it is why the id is a locator rather than a key.
"""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from services.clipper import anchor_identity
from services.clipper.dedupe import (
    _group as _group_indexes,
    _score_of,
    _text_of,
)

GROUP_VERSION = "moment_v1"

# How coarse a moment's identity is on the clock. Two runs that place the same
# payoff a second apart must agree; two moments ten seconds apart must not be
# confused. Six seconds matches `dedupe.SAME_PAYOFF_S`, which is the width the
# grouping itself already treats as one moment.
_QUANTUM_S = 6.0

# How many cuts of one moment the judge is shown. One, because the judge's
# question is WHICH MOMENT deserves a slot — the variant is chosen afterwards,
# by the scorer and diversity, which know things about rendering that the judge
# does not. Raising this trades pool width for cuts of moments already in it.
REPRESENTATIVES = 1

# The default budget, as shares of the pool. Deliberately not fitted: these are
# a starting point to be calibrated on a multi-genre corpus, and writing them
# as tuned numbers would invite the next reader to trust them.
SHARE_HEURISTIC = 0.50
SHARE_STORY = 0.25
# ...the remainder goes to coverage. Unused slots in either share above are
# redistributed rather than wasted.

CATEGORY_HEURISTIC = "heuristic"
CATEGORY_STORY = "story"
CATEGORY_COVERAGE = "coverage"
CATEGORIES: tuple[str, ...] = (CATEGORY_HEURISTIC, CATEGORY_STORY, CATEGORY_COVERAGE)

# How many stretches of the source coverage tries to spread across.
_TIME_BUCKETS = 12


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def _story_of(cand: dict) -> dict:
    story = cand.get("story")
    return story if isinstance(story, dict) else {}


def moment_id(cand: dict) -> str:
    """A stable locator for the moment a candidate is a cut of.

    Built from the payoff when there is one, because that is what makes two
    cuts the same moment — a tight cut and a story-rich cut of one joke share
    little text and the same payoff. Falls back to the window's midpoint, which
    is all a legacy candidate has.

    Quantised, so a model that places the same payoff at 100.4s and 101.9s
    produces one id and not two.
    """
    story = _story_of(cand)
    payoff = _num(story.get("payoff_t"), -1.0)
    if payoff >= 0:
        kind, anchor = "payoff", payoff
    else:
        kind = "span"
        anchor = (_num(cand.get("start")) + _num(cand.get("end"))) / 2.0
    bucket = int(anchor // _QUANTUM_S)
    raw = f"{GROUP_VERSION}|{kind}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def build_groups(cands: Sequence[dict], *, overlap_threshold: float = 0.4,
                 text_threshold: float = 0.62) -> list[dict]:
    """Candidates collapsed into the moments they are cuts of.

    The grouping itself is `dedupe._group`, unchanged and reused on purpose: it
    already compares time overlap, shared words and same payoff, and it already
    carries the measurement about comparing against the group LEADER rather
    than chaining through members. Writing a second grouping next to it would
    mean two answers to one question.

    What is new is the ID, the representative, and the facts the shortlist needs
    to spend a budget: is this moment story-backed, is its evidence grounded,
    which thread and archetypes it belongs to, and where it sits on the clock.
    """
    cands = [c for c in (cands or []) if isinstance(c, dict)]
    if not cands:
        return []

    order = sorted(range(len(cands)), key=lambda i: -_score_of(cands[i]))
    groups: list[dict] = []
    seen: dict[str, int] = {}

    for members in _group_indexes(cands, order, overlap_threshold, text_threshold):
        ranked = sorted(members, key=lambda i: -_score_of(cands[i]))
        # The group's STORY facts come from its best story member, not from
        # whichever cut scored highest. A moment can hold a legacy window and a
        # story window — they overlap, so the grouping puts them together — and
        # reading `is_story` off the leader made such a moment invisible to the
        # budget's story share whenever the legacy cut scored better. Which is
        # often: the heuristic is exactly what favours the loud, tidy cut.
        #
        # The REPRESENTATIVE follows the same choice, because the packet is
        # built from it: showing the judge the cut with no payoff, for a moment
        # selected because it has one, would undo the point of both.
        story_first = [i for i in ranked if _story_of(cands[i])]
        speaker = cands[(story_first or ranked)[0]]
        ranked = (story_first + [i for i in ranked if i not in set(story_first)]
                  if story_first else ranked)
        leader = cands[ranked[0]]
        story = _story_of(speaker)
        grounding = story.get("grounding") or {}
        anchor_ids = sorted({value for i in members
                             if (value := anchor_identity.of_candidate(cands[i]))})
        grounded_anchor_ids = sorted({
            value for i in members
            if (value := anchor_identity.of_candidate(cands[i]))
            and (_story_of(cands[i]).get("grounding") or {}).get("payoff")})

        base = moment_id(speaker)
        # Two groups CAN quantise onto the same id — different moments six
        # seconds apart that the grouping kept separate. Suffixed rather than
        # merged: the grouping looked at the text and the overlap and said no,
        # and an id collision is not a reason to overrule it.
        seen[base] = seen.get(base, 0) + 1
        mid = base if seen[base] == 1 else f"{base}-{seen[base]}"

        groups.append({
            "moment_id": mid,
            "anchor_id": anchor_ids[0] if len(anchor_ids) == 1 else None,
            "anchor_ids": anchor_ids,
            "grounded_anchor_ids": grounded_anchor_ids,
            "members": list(ranked),
            "representatives": ranked[:REPRESENTATIVES],
            # The score stays the group's BEST, whichever cut earned it: the
            # heuristic share is spending on how good the moment looks, not on
            # how good its story cut looks.
            "best_score": round(max(_score_of(cands[i]) for i in members), 3),
            "start": _num(leader.get("start")),
            "end": _num(leader.get("end")),
            "payoff_t": story.get("payoff_t"),
            "thread_id": story.get("thread_id"),
            "archetypes": [str(k) for k in (story.get("archetypes") or [])],
            "is_story": bool(story),
            "grounded": bool(grounding.get("payoff")),
            "validity": story.get("validity"),
        })
    return groups


# ── the shortlist ────────────────────────────────────────────────────────────

def _bucket(group: dict, duration: float) -> int:
    if duration <= 0:
        return 0
    return min(_TIME_BUCKETS - 1,
               int(_num(group.get("start")) / duration * _TIME_BUCKETS))


def build_shortlist(groups: Sequence[dict], *, budget: int = 80,
                    duration: float = 0.0,
                    share_heuristic: float = SHARE_HEURISTIC,
                    share_story: float = SHARE_STORY,
                    exclude: set[str] | None = None) -> dict:
    """Which moments the judge is asked about, and why each one is there.

    Returns `{"selected": [...], "categories": {...}, "reason": {moment_id: cat}}`.

    Three passes, and the last one is the point:

      1. the heuristic's best, because it is right often enough to be the
         largest single share;
      2. story moments whose payoff is GROUNDED — evidence the heuristic cannot
         see at all, which is the recall the judge is being paid to reach;
      3. coverage: greedily, the moment from a stretch of the stream, a thread
         or an archetype that nothing selected so far represents.

    Unused slots redistribute forward. A source with no grounded story moments
    spends that share on coverage rather than leaving the pool short.
    """
    # `exclude` is how a second round asks about moments the first one did not
    # reach. Filtered here rather than by the caller so every share sees the
    # same field and the budget is spent on what is actually still open.
    groups = [g for g in (groups or []) if isinstance(g, dict)
              and g.get("moment_id") not in (exclude or set())]
    budget = max(0, int(budget))
    if not groups or budget <= 0:
        return {"selected": [], "categories": {}, "reason": {}}

    # Everything fits: there is no shortlist to build, and pretending to build
    # one would only invent an ordering nobody asked for.
    if len(groups) <= budget:
        return {
            "selected": list(groups),
            "categories": {"all": len(groups)},
            "reason": {g["moment_id"]: "all" for g in groups},
        }

    picked: list[dict] = []
    taken: set[str] = set()
    reason: dict[str, str] = {}

    def take(group: dict, category: str) -> None:
        if group["moment_id"] in taken:
            return
        taken.add(group["moment_id"])
        reason[group["moment_id"]] = category
        picked.append(group)

    by_score = sorted(groups, key=lambda g: -_num(g.get("best_score")))
    for group in by_score[:int(budget * share_heuristic)]:
        take(group, CATEGORY_HEURISTIC)

    story = [g for g in by_score if g.get("is_story") and g.get("grounded")]
    want_story = int(budget * share_story)
    for group in story:
        if len(picked) >= int(budget * (share_heuristic + share_story)):
            break
        take(group, CATEGORY_STORY)
        want_story -= 1

    # Coverage. Walks the field best-first and takes anything that introduces a
    # stretch, a thread or an archetype nothing in the pool has yet, then falls
    # back to plain score once every axis is represented.
    seen_buckets = {_bucket(g, duration) for g in picked}
    seen_threads = {g.get("thread_id") for g in picked if g.get("thread_id")}
    seen_kinds = {k for g in picked for k in g.get("archetypes") or []}
    for group in by_score:
        if len(picked) >= budget:
            break
        if group["moment_id"] in taken:
            continue
        bucket = _bucket(group, duration)
        kinds = set(group.get("archetypes") or [])
        thread = group.get("thread_id")
        new = (bucket not in seen_buckets
               or (thread and thread not in seen_threads)
               or bool(kinds - seen_kinds))
        if not new:
            continue
        take(group, CATEGORY_COVERAGE)
        seen_buckets.add(bucket)
        if thread:
            seen_threads.add(thread)
        seen_kinds |= kinds

    for group in by_score:
        if len(picked) >= budget:
            break
        take(group, CATEGORY_COVERAGE)

    categories: dict[str, int] = {}
    for category in reason.values():
        categories[category] = categories.get(category, 0) + 1
    return {"selected": picked, "categories": categories, "reason": reason}


# ── what the judge is actually shown ─────────────────────────────────────────

def packet_for(cand: dict, group: dict, *, max_chars: int = 900) -> dict:
    """One moment, in the shape the judge is asked to check it in.

    The old packet was `id, archetype, start, first 900 characters` and nothing
    else, so the judge had to INFER where the context ended and the payoff began
    from prose — while being asked to rule on exactly that. Measured, truncation
    was not the problem: the median packet was 423 characters and 2 of 80 hit
    the limit. The missing markers were.

    Everything here is already known by the time the judge runs. None of it is
    a new measurement; it is the same evidence, named.
    """
    story = _story_of(cand)
    text = " ".join(str(cand.get("text") or "").split())
    start, end = _num(cand.get("start")), _num(cand.get("end"))

    return {
        "moment_id": group.get("moment_id"),
        "start": round(start, 1),
        "end": round(end, 1),
        "archetypes": group.get("archetypes") or [],
        "opening": text[:160],
        "text": text[:max_chars],
        "context": [
            {"t": round(_num(c.get("t")), 1),
             "fact": str(c.get("fact") or "")[:120],
             "grounded": bool(c.get("grounded"))}
            for c in (story.get("required_context") or []) if isinstance(c, dict)
        ][:4],
        "payoff": _payoff_view(story, start, end),
        "reaction_end": (round(_num(story.get("reaction_end")), 1)
                         if story.get("reaction_end") is not None else None),
        "metrics": {
            "hook_latency": story.get("hook_latency"),
            "context_debt": story.get("context_debt"),
            "confidence": story.get("confidence"),
        },
        "provenance": {
            "source": cand.get("payoff_source"),
            "validity": story.get("validity"),
            "prompt": (story.get("provenance") or {}).get("prompt_version"),
        },
    }


def _payoff_view(story: dict, start: float, end: float) -> dict | None:
    """Where the payoff sits, as a share of the clip and in seconds."""
    t = _num(story.get("payoff_t"), -1.0)
    if t < 0:
        return None
    span = max(0.001, end - start)
    return {
        "t": round(t, 1),
        "at": round(max(0.0, min(1.0, (t - start) / span)), 2),
        "grounded": bool(story.get("payoff_grounded")),
        "quote": str((story.get("payoff_evidence") or {}).get("quote") or "")[:160],
        "strength": story.get("payoff_strength"),
    }


def build_pool(refined: Sequence[dict], *, duration: float, budget: int,
               overlap_threshold: float = 0.4,
               text_threshold: float = 0.62,
               exclude: set[str] | None = None,
               groups: Sequence[dict] | None = None) -> dict:
    """Candidates in, the judge's pool out, with the counts worth recording.

    `groups` lets a second round reuse the FIRST round's grouping instead of
    recomputing it. That is not an optimisation, it is a correctness fix:
    `dedupe._group` walks the field in descending `overall` order and elects
    each group's leader by score, and `apply_ranking` has by then rewritten
    `overall` for everything in the first pool. Regrouping afterwards can hand a
    moment a different leader — and for a legacy candidate the moment id comes
    from the leader's own span, so the same moment can come back under a
    different id halfway through its own run.

    Returns `{"pool", "groups", "categories", "story"}`. `pool` holds the
    representative CANDIDATES — the same dicts, not copies — because that is
    what `judge` scores and what `apply_ranking` writes back onto. Each one is
    stamped with its `moment_id` and carries its packet on `_packet`.

    Lives here rather than in the worker so the whole selection is testable
    without a job, a queue or a database.
    """
    groups = (list(groups) if groups is not None
              else build_groups(refined, overlap_threshold=overlap_threshold,
                                text_threshold=text_threshold))
    chosen = build_shortlist(groups, budget=budget, duration=duration,
                             exclude=exclude)

    pool: list[dict] = []
    for group in chosen["selected"]:
        for index in group["representatives"]:
            cand = refined[index]
            cand["moment_id"] = group["moment_id"]
            cand["_packet"] = packet_for(cand, group)
            pool.append(cand)

    return {
        "pool": pool,
        "groups": len(groups),
        "all_groups": groups,
        # What THIS round asked about. `all_groups` is the whole field, so
        # excluding on it would make a second round ask about nothing.
        "selected_groups": chosen["selected"],
        "categories": chosen["categories"],
        "story": sum(1 for g in chosen["selected"] if g.get("is_story")),
        # The DENOMINATORS. `story` above is how many story moments the judge
        # was asked about; without these it has nothing to be a share of, and
        # "19 story moments judged" reads as success or failure depending
        # entirely on whether the field held 20 or 200.
        **story_census(groups, duration=duration),
    }


def story_census(groups: Sequence[dict], *, duration: float,
                 quarters: int = 4) -> dict:
    """How many story moments exist, how they validate, and where they sit.

    Counting, not deciding — nothing here reaches the pool. It exists because
    the pilot on non-Minecraft sources asks a question the run trace could not
    answer: does the story engine find moments in material with no gaming
    payoff, and does it find them THROUGHOUT the source or only where the
    energy is loudest.

    The temporal spread is the half that is easy to skip and expensive to lack.
    Batch 4 found the first three hours of a four-hour stream unread, and the
    only reason it was visible is that someone plotted payoff times by hand.

    PLACED BY `payoff_t`, NOT BY `start`. The moment's identity is its payoff —
    `moment_id` is built from it — and a cut carrying long context can begin a
    whole quarter before the thing it is about. Placing by the cut would report
    where the CAMERA started, which is not the question.

    `story_grounded` is about the PAYOFF's evidence and `story_valid` about the
    window; they are separate axes, and neither is the complement of the other.
    All three validity buckets are recorded because "4 uncertain" alone cannot
    say whether the other two are valid or invalid.
    """
    groups = [g for g in (groups or ()) if isinstance(g, dict)]
    story = [g for g in groups if g.get("is_story")]

    # None, not zeros: a source whose duration never probed has an UNKNOWN
    # spread, and four zeros is a shape — the flattest one there is.
    spread: list[int] | None = None
    if duration > 0:
        spread = [0] * quarters
        for group in story:
            at = _num(group.get("payoff_t"), -1.0)
            if at < 0:
                at = (_num(group.get("start")) + _num(group.get("end"))) / 2.0
            index = int(at / duration * quarters)
            spread[min(quarters - 1, max(0, index))] += 1

    def _validity(name: str) -> int:
        return sum(1 for g in story if g.get("validity") == name)

    return {
        # WHAT THE SHORTLIST SPENDS ON, not how many things the engine found.
        # A group is a dedupe group, and dedupe both splits and merges relative
        # to the anchors underneath: on a 63-minute interview 7 distinct payoffs
        # became 11 groups, while on a 4-hour stream 28 became 25. Comparing
        # sources on this number compares their dedupe behaviour as much as
        # their content.
        "story_groups": len(story),
        "story_grounded": sum(1 for g in story if g.get("grounded")),
        "story_valid": _validity("valid"),
        "story_uncertain": _validity("uncertain"),
        "story_invalid": _validity("invalid"),
        "story_quarters": spread,
        # WHAT THE ENGINE FOUND. New runs count the canonical anchor id; old
        # candidates without one retain the explicitly weaker payoff bucket.
        **_discoveries(story),
    }


def _discoveries(story: Sequence[dict]) -> dict:
    keys, grounded = anchor_identity.discovery_keys(story, _QUANTUM_S)
    return {"story_payoffs": len(keys),
            "story_payoffs_grounded": len(grounded)}


# Split out when this file crossed 500 lines; re-exported so the worker and the
# tests keep one import for "moments and their verdicts". Same pattern as
# `clipper_build` re-exporting from `clipper_finalize`.
from services.clipper.verdict_propagation import (  # noqa: E402,F401
    propagate_verdicts,
    _spread_refusal,
    _blend,
    _VERDICT_FIELDS,
)
