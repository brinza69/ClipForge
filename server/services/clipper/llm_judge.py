"""
ClipForge — AI Stream Clipper: judging the field, not each clip alone.

Absolute scoring compresses. Measured on 46 real candidates it answered with
eight distinct values, and on a quiet source everything landed between 5 and
40 — a ranking that cannot separate a good clip from a mediocre one. A forced
ordering cannot compress, and it is the question that actually matters: if
only N of these can be published, which N.

Split out of llm_select.py, which was approaching the repo's 500-line limit.
The seam is real: everything here reads FINISHED candidates and ranks them,
where llm_select proposes them in the first place. The engine plumbing —
`_ask`, `parse_json`, `apply_scores` — stays there and is imported.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from services.clipper.llm_select import (
    JUDGE_ENGINES, MAX_CLIP_CHARS, MAX_JUDGE_CLIPS, _ask, _num, apply_scores,
    parse_json,
)

logger = logging.getLogger("clipforge.clipper.llm_judge")


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



async def judge(cands: list[dict], *, weight: float = 0.5,
                engines: Sequence[str] = JUDGE_ENGINES,
                model: str | None = None, want: int = 8) -> int:
    """Rank candidates against each other and blend it in. 0 when unavailable."""
    if not cands:
        return 0
    # The best candidates so far, NOT the first ones on the clock. `cands` is
    # in timeline order, so slicing it handed the judge the opening minutes
    # and nothing else: measured on a 4-hour stream with 925 candidates, it
    # saw the first 80 — about twenty minutes — while everything after that
    # kept an unjudged heuristic score and won on it.
    subset = sorted(cands, key=lambda c: -_num(c.get("overall")))[:MAX_JUDGE_CLIPS]
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
