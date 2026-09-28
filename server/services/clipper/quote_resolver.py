"""Where a quote actually IS in the transcript — Batch S7's deterministic resolver.

`story_evidence.ground_claim` asks a yes/no question: is this quote inside the
atom the claim's timestamp lands in, plus one neighbour either side. That is a
window of about eighteen seconds, atoms having a median length of 6.2s.

MEASURED ON SIX SOURCES, 164 claims: 64.0% pass. Of the rest, **23.2% are
verbatim somewhere within ±120s** and another 6.7% appear in order with gaps.
So for nearly a third of all claims the words are really there and the POINTER
is what drifted — and today that case is indistinguishable from a model that
invented a sentence, because both come back `grounded: False`.

THIS MODULE SEPARATES THOSE TWO, and nothing else. It does not decide whether a
claim is true, it does not widen any window, and it does not change what ships.
It answers one question — where does this quote occur — and records the answer
with its distance from where the claim said it was.

WHY IT COMES BEFORE WIDENING THE WINDOW, which is the obvious cheaper move.
Going from one neighbour to three would be choosing a number because it makes
the figure go up, and this repository has spent whole sessions undoing exactly
that. The drift distribution is the evidence a window size should be derived
FROM: if the misses cluster at a small drift, the window is the answer and the
number comes out of the data; if they scatter, the window was never the problem
and widening it would have hidden that.

AMBIGUITY IS A REAL ANSWER, and it is the guard that makes the rest safe. A
phrase like "you know what I mean" occurs in a three-hour stream dozens of
times. Picking the occurrence nearest the claim would MANUFACTURE a grounding
that the evidence does not support — the same shape as a check consulting the
representation it is checking. So a quote found more than once is `ambiguous`,
never bound, and the count travels with it.

IT SHARES ONE NOTION OF "THE SAME WORDS" with what ships. Normalisation and
whole-token matching come from `story_evidence`; a second tokeniser here would
drift from the first and the comparison would stop meaning anything. There is a
test that asserts the two agree.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.story_evidence import normalise_quote

__all__ = ["BOUND", "AMBIGUOUS", "ABSENT", "UNRESOLVABLE", "STATES",
           "MAX_OCCURRENCES", "resolve"]

#: Found exactly once in the transcript. The only state that locates anything.
BOUND = "bound"
#: Found more than once. NOT a weaker bind — nothing here can say which one the
#: claim meant, and choosing the nearest would invent the answer.
AMBIGUOUS = "ambiguous"
#: Found nowhere. Still not "invented": a paraphrase, a mis-transcription and a
#: fabrication all land here, and this module cannot separate them.
ABSENT = "absent"
#: There was nothing to resolve — no quote, or no atoms to resolve against.
UNRESOLVABLE = "unresolvable"
STATES: tuple[str, ...] = (BOUND, AMBIGUOUS, ABSENT, UNRESOLVABLE)

#: How many occurrence times to keep. A filler phrase can occur hundreds of
#: times and the exact number stops mattering once it is clearly not unique —
#: but the COUNT is still reported in full, because "more than a few" and "two"
#: are different findings.
MAX_OCCURRENCES = 8


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out or out in (float("inf"), float("-inf")) else out


def _timeline(atoms: Sequence[dict]) -> tuple[list[str], list[float]]:
    """Every token in timeline order, and the time each one belongs to.

    ONE FLAT SEQUENCE, not a per-atom search, because `ground_claim` joins the
    atoms it chose before matching — so a quote is allowed to straddle an atom
    boundary and this has to allow the same thing or it would answer a
    different question.

    A token's time is the START of the atom it came from. Atoms are 6.2s at the
    median, so this is a location to within an atom and is not claimed to be
    finer; `drift_s` inherits that resolution and nothing downstream should read
    it as sub-second.
    """
    tokens: list[str] = []
    times: list[float] = []
    ordered = sorted((a for a in atoms if isinstance(a, dict)),
                     key=lambda a: _num(a.get("start")) or 0.0)
    for atom in ordered:
        start = _num(atom.get("start"))
        if start is None:
            # A time nobody can read is not time zero. The atom's words would
            # otherwise be searchable at a position that does not exist.
            continue
        words = normalise_quote(atom.get("text")).split()
        tokens.extend(words)
        times.extend([start] * len(words))
    return tokens, times


def _positions(haystack: Sequence[str], needle: Sequence[str]) -> list[int]:
    """Every index where `needle` appears as a contiguous run of WHOLE tokens.

    The plural of `story_evidence._contains_sequence`, and deliberately the same
    rule: not a substring test on joined text, which would pass "a bla" against
    "a blast furnace".
    """
    n, m = len(haystack), len(needle)
    if not m or m > n:
        return []
    first = needle[0]
    return [i for i in range(n - m + 1)
            if haystack[i] == first and list(haystack[i:i + m]) == list(needle)]


def resolve(quote: Any, atoms: Sequence[dict] | None,
            *, claimed_t: Any = None) -> dict:
    """`quote_resolution_v1`: where this quote is, and how far that is from
    where the claim said it was.

    RECORDED, APPLIED TO NOTHING. It changes no grounding verdict and no board;
    it exists so the next decision — whether the search window is too tight —
    can be taken from a distribution instead of from a guess.
    """
    out: dict[str, Any] = {
        "schema": "quote_resolution_v1",
        "state": UNRESOLVABLE,
        "why": None,
        "occurrences": 0,
        "at": [],
        "matched_t": None,
        "claimed_t": _num(claimed_t),
        # None rather than 0.0 wherever there is nothing to subtract. A drift of
        # zero is a claim that landed exactly right, which is the opposite of
        # not knowing.
        "drift_s": None,
        "applied": False,
    }
    needle = normalise_quote(quote).split()
    if not needle:
        # An empty quote matches everything by definition, which would make
        # every claim resolvable and the measurement a formality.
        out["why"] = "no_quote"
        return out
    if not atoms:
        out["why"] = "no_atoms"
        return out

    tokens, times = _timeline(atoms)
    if not tokens:
        out["why"] = "no_readable_atoms"
        return out

    found = _positions(tokens, needle)
    out["occurrences"] = len(found)
    out["at"] = [round(times[i], 3) for i in found[:MAX_OCCURRENCES]]
    if not found:
        out["state"] = ABSENT
        return out
    if len(found) > 1:
        out["state"] = AMBIGUOUS
        return out

    out["state"] = BOUND
    matched = times[found[0]]
    out["matched_t"] = round(matched, 3)
    claimed = out["claimed_t"]
    if claimed is not None and claimed >= 0:
        out["drift_s"] = round(matched - claimed, 3)
    return out
