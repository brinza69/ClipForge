"""Whether a chosen window is a COMPLETE thought — Batch R5.

Recorded, applied to nothing, and that is the plan's own instruction rather than
this file's caution: `eligibility` starts influencing the board only after this
validator has passed the corpus. Until then every verdict is written beside the
candidate and the selection is exactly what it was.

WHAT THIS IS NOT. `candidate_boundaries.refine_boundaries` already MOVES the two
edges — sentence snap, lead-in, payoff, reaction, answer, tail trim, orphan
drop, release pad — and `candidates.extract_features` already scores them.
Neither of those answers the question R5 asks, which is whether the window that
came out is finished. A score is a number to rank by; this is a verdict with the
evidence attached.

THE HOLE IT CLOSES, and it is the same one every batch since R0 has found.
`extract_features` computes `ends_on_sentence = 1.0 if text.endswith(".!?…")`.
On a transcript WITHOUT punctuation that is 0.0 for every clip ever cut — and
0.0 there does not mean "ends mid-sentence", it means nobody could tell. The
clipper transcribes with `keep_punctuation=True`, but `transcriber._clean_text`
strips punctuation by default and an older transcript may carry none, so the
distinction is not hypothetical. Here a source with no sentence-final
punctuation makes the sentence checks `unavailable`, never `incomplete`.

ONE BOUNDED REPAIR, per §3.5. The proposal may extend the end once, to the next
sentence end, inside the maximum duration and without running into the next
candidate. It is not applied, and if it cannot be made the window is marked
ineligible with the reason — never repaired twice, never nudged until it passes.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.candidate_boundaries import _context_floor
from services.clipper.candidate_terms import (
    DANGLE_PAUSE_S, PAUSE_KEEP_S, SENTENCE_EDGE_S, TAIL_PAD_S,
    _EPS, _LEAD_IN, _num, _source, _tokens,
)
# THE canonical "is this the end of a sentence" and "has the speaker finished a
# thought on this word". Both already decide real cuts in `segmentation` and
# `candidate_boundaries`; a second opinion here would be a second rule.
from services.clipper.segmentation import _continues, _ends_sentence
# The render audit's threshold for "no air after the last word", imported rather
# than restated so the selection side and the render side count the SAME defect.
# R0 measured 22 of the 58 pilot exports at or under it.
from services.clipper.edit_quality import TAIL_TIGHT_S

__all__ = ["STATUSES", "DEFECTS", "UNKNOWNS", "BLOCKING", "TECHNICAL",
           "REFUSALS", "completeness", "eligibility", "boundary_view", "attach"]

COMPLETE = "complete"
INCOMPLETE = "incomplete"
UNAVAILABLE = "unavailable"
STATUSES: tuple[str, ...] = (COMPLETE, INCOMPLETE, UNAVAILABLE)

# --- what can be wrong with an edge, as a closed list ------------------------

#: The cut lands strictly inside a word. The gate's "zero truncated words", and
#: the one defect that is a defect on any material in any language.
START_IN_WORD = "start_inside_word"
END_IN_WORD = "end_inside_word"
#: The window opens or closes part-way through a sentence.
START_MID_SENTENCE = "start_mid_sentence"
END_MID_SENTENCE = "end_mid_sentence"
#: The first word is one the speaker cannot have started a thought on — "and",
#: "so", "but", "it". Not a defect on its own; a hook may legitimately open in
#: the middle of one.
START_ON_CONTINUATION = "start_on_continuation"
#: The last word is a continuation word with real silence after it: the speaker
#: stopped, but not on a finished thought. Measured on a real stream: clips
#: ended "...of water bro let's" with the "go" sixteen seconds later.
ORPHAN_TAIL = "orphan_tail"
#: No air after the last word. The release of the final consonant is cut, and
#: the clip is heard as ending mid-phrase even when the words are all there.
CLIPPED_RELEASE = "clipped_release"
#: More silence at the end than `PAUSE_KEEP_S`, which is dead air rather than
#: rhythm.
DEAD_TAIL = "dead_tail"
#: A fact the story path said this moment requires sits before the window opens.
CONTEXT_OUTSIDE = "required_context_outside"

DEFECTS: tuple[str, ...] = (
    START_IN_WORD, START_MID_SENTENCE, START_ON_CONTINUATION,
    END_IN_WORD, END_MID_SENTENCE, ORPHAN_TAIL, CLIPPED_RELEASE, DEAD_TAIL,
    CONTEXT_OUTSIDE,
)

#: Which defects make a window INELIGIBLE rather than merely imperfect. CHOSEN,
#: from the gate's own words — "zero cuvinte trunchiate" and a window that is a
#: complete thought — and named here so the calibration has one thing to change.
#:
#: `START_MID_SENTENCE` is deliberately absent: opening mid-sentence is what a
#: hook often does, and the gate asks a HUMAN whether the opening is acceptable.
#: `CLIPPED_RELEASE` is absent too — it is fixed by padding, which
#: `_keep_release` already does, not by refusing the moment.
BLOCKING: frozenset[str] = frozenset({START_IN_WORD, END_IN_WORD,
                                      END_MID_SENTENCE, ORPHAN_TAIL})

#: Defects about the FILE rather than the moment: a pad at render time fixes
#: them and the selection never has to change. Kept as their own axis because
#: whether one may reach a board is R7's preflight question — answering it here
#: by folding them into `eligible` would decide a gate this batch does not own.
TECHNICAL: frozenset[str] = frozenset({CLIPPED_RELEASE, DEAD_TAIL})

# --- why a check could not be made -------------------------------------------

NO_WORDS = "no_words_in_window"
#: The transcript carries no sentence-final punctuation anywhere. Every sentence
#: check is then unanswerable, and answering it anyway would report a whole
#: corpus as ending mid-sentence because of how it was transcribed.
NO_PUNCTUATION = "transcript_without_punctuation"
UNKNOWNS: tuple[str, ...] = (NO_WORDS, NO_PUNCTUATION)

# --- why the one repair was not made -----------------------------------------

REPAIR_EXTEND = "extend_to_sentence_end"
NOTHING_TO_REPAIR = "nothing_to_repair"
NO_SENTENCE_END = "no_sentence_end_in_reach"
WOULD_EXCEED_MAX = "would_exceed_max_duration"
WOULD_OVERLAP_NEXT = "would_overlap_the_next_window"
NOT_REPAIRABLE_HERE = "defect_is_not_an_unfinished_end"
REFUSALS: tuple[str, ...] = (NOTHING_TO_REPAIR, NO_SENTENCE_END,
                             WOULD_EXCEED_MAX, WOULD_OVERLAP_NEXT,
                             NOT_REPAIRABLE_HERE, UNAVAILABLE)

#: Silence left between a repaired end and the next window's first word. Same
#: role as `RELEASE_GAP_S` one level up: a repair that touches the next clip has
#: not repaired anything.
OVERLAP_GAP_S = 0.05


def _straddled(words: Sequence[dict], t: float) -> dict | None:
    """The word a cut at `t` would land inside, or None.

    Not `_neighbourhood`, which drops a straddling word from all three of its
    lists — it is neither before the cut, after it, nor wholly inside — so the
    one word this check exists to find is exactly the one that pass cannot see.
    """
    for w in words or []:
        w0, w1 = _num(w.get("start")), _num(w.get("end"), _num(w.get("start")))
        if w0 < t - _EPS and w1 > t + _EPS:
            return w
        if w0 >= t:
            break
    return None


def _inside(words: Sequence[dict], start: float, end: float) -> list[dict]:
    return [w for w in words or []
            if _num(w.get("start")) >= start - _EPS
            and _num(w.get("end"), _num(w.get("start"))) <= end + _EPS]


def _punctuated(words: Sequence[dict]) -> bool:
    return any(_ends_sentence(str(w.get("word") or "")) for w in words or [])


def _sentence_over(sentences: Sequence[dict], t: float) -> dict | None:
    """The sentence containing `t`, or the first one starting after it."""
    for s in sentences or []:
        if _num(s.get("end")) > t + _EPS:
            return s
    return None


def _next_sentence_end(sentences: Sequence[dict], t: float) -> float | None:
    for s in sentences or []:
        end = _num(s.get("end"))
        if end > t + _EPS:
            return end
    return None


def completeness(cand: dict, words: Sequence[dict]) -> dict:
    """`{start, end, defects, unknown, measurements}` for one window.

    Every list is closed and every measurement is a number somebody can check.
    A defect is never inferred from the absence of evidence: with no words in
    the window, or no punctuation in the source, the affected checks come back
    as unknowns and the status is `unavailable` rather than `incomplete`.
    """
    cand = cand if isinstance(cand, dict) else {}
    start, end = _num(cand.get("start")), _num(cand.get("end"))
    inside = _inside(words, start, end)
    punctuated = _punctuated(words)
    sentences = _source(list(words or []))["sentences"] if words else []

    start_defects: list[str] = []
    end_defects: list[str] = []
    unknown: list[str] = []
    measured: dict[str, Any] = {"words_inside": len(inside)}

    # A cut inside a word is measurable with or without punctuation, and it is
    # the one thing the gate calls out by name.
    if _straddled(words, start) is not None:
        start_defects.append(START_IN_WORD)
    if _straddled(words, end) is not None:
        end_defects.append(END_IN_WORD)

    if not inside:
        unknown.append(NO_WORDS)
        measured["tail_s"] = None
        measured["lead_s"] = None
    else:
        first, last = inside[0], inside[-1]
        # How much air the window leaves around the speech it contains.
        measured["lead_s"] = round(_num(first.get("start")) - start, 3)
        tail = round(end - _num(last.get("end"), _num(last.get("start"))), 3)
        measured["tail_s"] = tail
        if tail <= TAIL_TIGHT_S:
            end_defects.append(CLIPPED_RELEASE)
        elif tail > PAUSE_KEEP_S:
            end_defects.append(DEAD_TAIL)

        tokens = _tokens(inside)
        if tokens and tokens[0] in _LEAD_IN:
            start_defects.append(START_ON_CONTINUATION)

        # An orphan is a continuation word with real silence after it. The
        # silence is what proves it: a window that simply runs into the next
        # sentence is not orphaned, it is merely cut early.
        if _continues(str(last.get("word") or "")):
            following = next(
                (w for w in words or []
                 if _num(w.get("start")) > _num(last.get("end")) + _EPS), None)
            gap = (None if following is None
                   else round(_num(following.get("start"))
                              - _num(last.get("end")), 3))
            measured["gap_after_last_word_s"] = gap
            if gap is not None and gap >= DANGLE_PAUSE_S:
                end_defects.append(ORPHAN_TAIL)

    if not punctuated:
        unknown.append(NO_PUNCTUATION)
    elif inside:
        # STRUCTURAL, not a tolerance: does the window's first word BEGIN a
        # sentence. A time comparison against the sentence's start called a
        # clean opening mid-sentence whenever the window opened in the silence
        # before the speech — which is what `refine_boundaries` produces every
        # time it adds a lead-in or pads a start. The score asks a different
        # question on purpose (how CLOSE the cut is, a continuous quality); this
        # asks a fact.
        opening = _sentence_over(sentences, _num(inside[0].get("start")))
        if opening is None or _num(opening.get("start")) < _num(
                inside[0].get("start")) - _EPS:
            start_defects.append(START_MID_SENTENCE)
        if not _ends_sentence(str(inside[-1].get("word") or "")):
            end_defects.append(END_MID_SENTENCE)

    # The story path's own requirement, read rather than recomputed: `story.py`
    # chose the opening around these facts and `story_evidence` remeasures them
    # after every move. A fact before the window is context the moment needs and
    # no longer contains.
    floor = _context_floor(cand)
    if floor is not None and floor < start - _EPS:
        start_defects.append(CONTEXT_OUTSIDE)
        measured["required_context_at"] = round(floor, 3)

    def _status(defects: list[str], blocked_by_unknown: bool) -> str:
        if defects:
            return INCOMPLETE
        return UNAVAILABLE if blocked_by_unknown else COMPLETE

    blind = bool(unknown)
    return {
        "start": {"status": _status(start_defects, blind),
                  "defects": start_defects},
        "end": {"status": _status(end_defects, blind), "defects": end_defects},
        "defects": start_defects + end_defects,
        "unknown": unknown,
        "measurements": measured,
    }


def _repair(cand: dict, words: Sequence[dict], defects: Sequence[str], *,
            max_s: float, ceiling: float | None,
            next_start: float | None) -> dict:
    """The ONE bounded proposal, or the reason there is none.

    Extending the end to the next sentence end is the only repair R5 allows,
    and it is allowed once. Everything else — a truncated word, a window that
    opens without its context — is either already handled upstream or is a
    reason to refuse the moment, not to keep nudging it until it passes.
    """
    start, end = _num(cand.get("start")), _num(cand.get("end"))
    wanted = [d for d in defects if d in (END_MID_SENTENCE, ORPHAN_TAIL)]
    if not wanted:
        # Two different answers, and they were one. "Nothing is wrong" and
        # "something is wrong and this repair does not touch it" are not the
        # same report, and only the second is a reason to look further.
        return {"kind": None,
                "refused": NOT_REPAIRABLE_HERE if defects else NOTHING_TO_REPAIR}

    sentences = _source(list(words or []))["sentences"] if words else []
    target = _next_sentence_end(sentences, end)
    if target is None:
        return {"kind": None, "refused": NO_SENTENCE_END}

    # Bounded, in the order the plan bounds it: the maximum duration first,
    # then the media, then the neighbour.
    if target - start > max_s + _EPS:
        return {"kind": None, "refused": WOULD_EXCEED_MAX,
                "would_be_s": round(target - start, 3), "max_s": max_s}
    if ceiling is not None and target > ceiling + _EPS:
        return {"kind": None, "refused": NO_SENTENCE_END}
    if next_start is not None and target > next_start - OVERLAP_GAP_S:
        return {"kind": None, "refused": WOULD_OVERLAP_NEXT,
                "next_start": round(next_start, 3)}

    # What the repair would leave behind, measured on the repaired window rather
    # than assumed: an extension that reaches a sentence end can still land on
    # an orphan, and claiming otherwise would be the "nudge until it passes"
    # this batch is not allowed to do.
    after = completeness({**cand, "end": target}, words)
    return {
        "kind": REPAIR_EXTEND,
        "end": round(target, 3),
        "added_s": round(target - end, 3),
        "clears": [d for d in wanted if d not in after["defects"]],
        "remaining": list(after["defects"]),
    }


def eligibility(defects: Sequence[str], unknown: Sequence[str],
                repair: dict) -> tuple[bool | None, str | None]:
    """`(eligible, why not)` from what was found. THE rule, in one place.

    Derived, not decided: given the defects, the unknowns and the one repair,
    there is exactly one correct verdict. It lives here as a function because
    the audit has to check the record against it — validating the TYPE of
    `eligible` while leaving its VALUE unchecked let a clean view claim any
    verdict it liked, which is the same hole as a `blocking` list nobody
    reconciled with its own defects.

    ORDER IS THE WHOLE RULE. A defect that was MEASURED rejects the window
    whatever else could not be checked: a cut inside a word needs neither
    punctuation nor a language, and letting `unknown` swallow it hid a certain
    defect behind an unavailable measurement. `None` is right only when nothing
    known rejects the candidate and the verdict depends on the missing signal.
    """
    blocking = [d for d in defects if d in BLOCKING]
    # `repair.get("remaining") or []` turned a MISSING `remaining` into an empty
    # one, so a repair that never said what it left behind counted as having
    # cleared everything — and a window with a truncated word came back
    # eligible. Absence of evidence that the blocker was cleared is not evidence
    # that it was. The list has to be there and be a list.
    remaining = repair.get("remaining")
    repaired = (bool(repair.get("kind")) and isinstance(remaining, list)
                and not [d for d in remaining if d in BLOCKING])
    if blocking and not repaired:
        return False, blocking[0]
    if unknown:
        # Nothing known rejects it, and the check that would decide could not be
        # made. The board must not lose a moment for the way its transcript was
        # written.
        return None, UNAVAILABLE
    return True, None


def boundary_view(cand: dict, words: Sequence[dict], *, max_s: float,
                  ceiling: float | None = None,
                  next_start: float | None = None) -> dict:
    """The whole verdict for one candidate, auditable on its own terms.

    RECORDED, APPLIED TO NOTHING. `eligible` is a field in an artefact until the
    validator has passed the corpus — the plan says so in as many words, and the
    reason is that a window this refuses is a moment the board loses. A rule
    that silently removes moments has to be measured before it is trusted, not
    after.
    """
    checks = completeness(cand, words)
    defects = checks["defects"]
    blocking = [d for d in defects if d in BLOCKING]
    repair = (_repair(cand, words, defects, max_s=max_s, ceiling=ceiling,
                      next_start=next_start)
              if defects else {"kind": None, "refused": NOTHING_TO_REPAIR})

    eligible, reason = eligibility(defects, checks["unknown"], repair)

    return {
        "schema": "boundary_view_v1",
        "scope": "window_completeness_and_one_bounded_repair",
        "start_s": round(_num(cand.get("start")), 3),
        "end_s": round(_num(cand.get("end")), 3),
        "start": checks["start"],
        "end": checks["end"],
        "defects": defects,
        # TWO AXES, NOT ONE VERDICT. `blocking` is about the MOMENT — is this
        # window worth keeping at all. `technical` is about the FILE, and a pad
        # at render time fixes it without touching the selection. Whether a
        # technical defect may reach a board is R7's preflight question, not
        # this batch's, and compressing them into one number would answer it
        # here by accident.
        "blocking": blocking,
        "technical": [d for d in defects if d in TECHNICAL],
        "unknown": checks["unknown"],
        "measurements": checks["measurements"],
        "repair": repair,
        "eligible": eligible,
        "ineligible_because": reason if eligible is False else None,
        # The pad the release rule aims for, recorded beside the tail it
        # produced. INHERITED from one source's median 0.16s / p90 0.40s, and
        # the audit prints the corpus distribution so it can be re-derived
        # rather than kept because it is already written down.
        "tail_pad_target_s": TAIL_PAD_S,
        "tail_tight_s": TAIL_TIGHT_S,
        # Never true here. The plan gates it: eligibility influences the board
        # only after this validator has passed the corpus.
        "applied": False,
    }


def attach(candidates: Sequence[dict], words: Sequence[dict], *,
           max_s: float, duration: float | None = None) -> list[dict]:
    """Record a verdict on every candidate, in place. Returns the same list.

    THE one place the per-candidate arguments are worked out, so the worker that
    writes the artefact and the audit that reads it cannot disagree about what
    was measured. They used to be two call sites, and the audit could only
    aggregate what the worker had already stored — which meant a corpus scored
    before this batch could not be measured at all without re-running it.

    `next_start` is the first window that begins at or after this one ENDS. A
    window starting exactly on the end is a neighbour like any other, and `>`
    quietly excluded it. It is a CONSERVATIVE bound rather than a correct one:
    the field is not a timeline, and the next candidate is often another variant
    of the same moment rather than a different one. Measured on the corpus, many
    windows have another start within half a second of their end, so this
    refuses repairs a grouping pass would allow. Recorded as
    `would_overlap_the_next_window` so the cost of the bound is countable
    instead of invisible.
    """
    rows = [c for c in candidates or [] if isinstance(c, dict)]
    starts = sorted(_num(c.get("start")) for c in rows)
    for cand in rows:
        end = _num(cand.get("end"))
        after = next((s for s in starts if s >= end - _EPS), None)
        cand["boundary_view"] = boundary_view(
            cand, words, max_s=max_s, ceiling=duration, next_start=after)
    return rows
