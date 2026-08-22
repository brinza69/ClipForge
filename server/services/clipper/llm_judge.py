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

# From llm_engine, NOT llm_select: llm_select re-exports this module at its own
# bottom, so importing it here made the two mutually dependent and the package
# order-dependent — `import llm_judge` first raised ImportError.
from services.clipper.llm_engine import (
    JUDGE_ENGINES, MAX_CLIP_CHARS, MAX_JUDGE_CLIPS, _ask, _num, parse_json,
)

logger = logging.getLogger("clipforge.clipper.llm_judge")


JUDGE_PROMPT_VERSION = "judge_v2_comparative"

# One judging call per run, but it still needs a request id: the trace groups
# attempts by it, and a bare stage name would not join up with the parse result
# recorded after the answer comes back.
JUDGE_REQUEST = "judge#0"

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
        packet = cand.get("_packet") if isinstance(cand.get("_packet"), dict) else None
        if packet is None:
            # No group behind this one — the legacy shape, unchanged.
            text = " ".join(str(cand.get("text") or "").split())[:MAX_CLIP_CHARS]
            tags = (cand.get("story") or {}).get("archetypes") or []
            kinds.update(tags)
            label = f" <{'+'.join(tags)}>" if tags else ""
            body.append(f"{i}.{label} [{_num(cand.get('start')):.0f}s] {text}")
            continue
        kinds.update(packet.get("archetypes") or [])
        body.append(_render_packet(i, packet))

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
        "Where a candidate lists PAYOFF and NEEDS lines, those are what the "
        "analysis believes the moment turns on and what a viewer must already "
        "know. A payoff marked `unverified` was not found word-for-word in the "
        "transcript — weigh it as a claim, not as a fact.\n"
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


def _render_packet(index: int, packet: dict) -> str:
    """One moment as labelled evidence rather than a wall of prose.

    The judge is asked whether a clip has a setup, a turn and a payoff. Handed
    only the transcript, it had to work out where each of those was before it
    could rule on them — from the same text it was ruling on. Naming them costs
    a few tokens per candidate and removes the guess.

    Every line is optional: a legacy candidate has no context and no payoff
    time, and printing empty headings would teach the model that "no payoff"
    is a formatting artefact rather than a finding.
    """
    tags = "+".join(packet.get("archetypes") or [])
    lines = [f"{index}." + (f" <{tags}>" if tags else "")
             + f" [{packet['start']:.0f}s-{packet['end']:.0f}s]"]

    payoff = packet.get("payoff")
    if payoff:
        where = f"{int(payoff['at'] * 100)}% in"
        mark = "quoted" if payoff.get("grounded") else "unverified"
        quote = f' "{payoff["quote"]}"' if payoff.get("quote") else ""
        lines.append(f"   PAYOFF at {payoff['t']:.0f}s ({where}, {mark}):{quote}")
    else:
        lines.append("   PAYOFF: none identified")

    for item in packet.get("context") or []:
        mark = "" if item.get("grounded") else " (unverified)"
        lines.append(f"   NEEDS at {item['t']:.0f}s{mark}: {item['fact']}")

    metrics = packet.get("metrics") or {}
    bits = []
    if metrics.get("hook_latency") is not None:
        bits.append(f"hook after {float(metrics['hook_latency']):.1f}s")
    if metrics.get("context_debt") is not None:
        bits.append(f"context debt {float(metrics['context_debt']):.2f}")
    if bits:
        lines.append("   " + ", ".join(bits))

    lines.append(f"   TRANSCRIPT: {packet.get('text', '')}")
    return "\n".join(lines)


def _base_score(cand: dict) -> float:
    """The heuristic reading a verdict is blended against.

    Captures it on first use when the caller did not: `apply_ranking` is also
    reached from tests and from the scored path, and without a named base the
    blend is against whatever `overall` happens to hold.
    """
    base = cand.get("heuristic_score")
    if base is None:
        base = _num(cand.get("overall"))
        cand["heuristic_score"] = base
    return _num(base)


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
        # The same number under the name the score model uses. `llm_score` is
        # kept because every existing reader and artifact uses it.
        cand["judge_score"] = round(score, 1)
        cand["llm_rank"] = position + 1
        cand["llm_verdict"] = {
            "story_editor": verdict.get("story_editor"),
            "cold_viewer": verdict.get("cold_viewer"),
            "reject_reasons": rejects,
            "prompt_version": JUDGE_PROMPT_VERSION,
        }
        if verdict.get("why"):
            cand["llm_reason"] = str(verdict["why"])[:200]
        # Blended against the HEURISTIC reading, captured here if the caller did
        # not. Blending against `overall` works exactly once: a second pool
        # round, or any caller that scores twice, compounds a verdict into a
        # number that already holds one — and the base of the blend stops being
        # a thing anyone can name.
        base = _base_score(cand)
        cand["overall"] = round((1.0 - w) * base + w * score, 2)
        # The number the selection rule ranks on, named. Set HERE as well as in
        # the propagation, or the pool's own members are the only judged
        # candidates without one.
        cand["selection_score"] = cand["overall"]
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
            cand["judge_score"] = 0.0
            cand["overall"] = round((1.0 - w) * _base_score(cand), 2)
            cand["selection_score"] = cand["overall"]
    return hit



async def judge(cands: list[dict], *, weight: float = 0.5,
                engines: Sequence[str] = JUDGE_ENGINES,
                model: str | None = None, want: int = 8,
                trace: Any = None, shortlist: list[dict] | None = None) -> int:
    """Rank candidates against each other and blend it in. 0 when unavailable."""
    if not cands:
        return 0
    # `shortlist`, when the caller built one, is a pool of MOMENTS chosen on a
    # budget — see candidate_groups. Without one this falls back to the best by
    # heuristic score, which is what shipped and what the budget replaces: it
    # ranks variants rather than moments, and it ranks by the very score the
    # judge exists to correct.
    #
    # Either way it is not the first candidates on the clock. `cands` is in
    # timeline order, so slicing it handed the judge the opening minutes and
    # nothing else: measured on a 4-hour stream with 925 candidates, it saw
    # about twenty minutes while everything after kept an unjudged heuristic
    # score and won on it.
    subset = shortlist if shortlist is not None else sorted(
        cands, key=lambda c: -_num(c.get("overall")))[:MAX_JUDGE_CLIPS]
    # The request id must match the one `_note_result` uses below, or the
    # attempt and its parse outcome are filed under two different calls and
    # `unusable` can never line up with `exhausted`.
    answer = await _ask(engines, judge_prompt(subset, want), model=model,
                        trace=trace, stage="judge", request=JUDGE_REQUEST)
    if answer is None:
        return 0
    verdicts = parse_json(answer)
    if trace is not None:
        from services.clipper.llm_engine import _note_result
        _note_result(trace, "judge", JUDGE_REQUEST, verdicts is not None)
    # A model that ignored "rank all of them" and scored them instead is still
    # useful, but the two answers are told apart by SHAPE, not by whether the
    # first parse succeeded: a scored answer carries ids too, so ranking it by
    # position would silently replace its scores with their order.
    scored = isinstance(verdicts, list) and any(
        isinstance(v, dict) and v.get("score") is not None
        and not any(k in v for k in ("story_editor", "cold_viewer", "critic"))
        for v in verdicts)
    if scored:
        # Imported here rather than at the top: it lives in llm_select, and
        # this module is what llm_select re-exports.
        from services.clipper.llm_select import apply_scores

        hit = apply_scores(subset, verdicts, weight=weight)
    else:
        hit = apply_ranking(subset, verdicts, weight=weight)
    logger.info("llm_select: judged %d of %d candidates (%s)",
                hit, len(subset), JUDGE_PROMPT_VERSION)
    if trace is not None:
        # The pair that matters: how many were EVALUATED against how many
        # existed. On the four-hour source in the audit that was 80 against
        # 920, and the number had to be recomputed months later because no
        # artefact recorded it.
        trace.note_count("judge_pool", len(subset))
        trace.note_count("judge_field", len(cands))
        trace.note_count("judge_hits", hit)
    return hit
