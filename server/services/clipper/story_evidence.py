"""
ClipForge — AI Stream Clipper: one representation of the narrative evidence.

THE DEFECT THIS CLOSES. Three parts of the pipeline reasoned about a moment and
none of them used the same evidence:

  * `story.py` produced a SEMANTIC payoff — the model naming what happened;
  * `candidates.extract_features` derived a MECHANICAL one from audio peaks and
    cue words, and every payoff feature (`payoff_position`, `setup_ratio`,
    `payoff_strength`, `post_payoff_energy`) described that one;
  * `candidate_boundaries` cut the clip around the mechanical one too.

Measured on the two audited sources, the gap between the two payoffs had a
median of 7.14s and 4.96s and a maximum of 26.5s and 61.6s. A clip could be
CHOSEN for one event and CUT around another.

The second half is staleness. `candidate_proposals` measured context debt and
hook latency against the window it proposed; `refine_boundaries` then moved both
ends and copied the `story` block verbatim. On the four-hour source, 20 of the
story candidates ended up with required context outside their own final span,
and their recorded metrics described a clip that no longer existed.

WHAT IS PURE HERE. Everything. No DB, no network, no model. Grounding is a
string and interval comparison against the atoms artifact, which is what lets
the whole contract be tested without a provider.

GROUNDING IS DETERMINISTIC AND IT MARKS RATHER THAN DROPS. A claim is grounded
when the model named atoms that exist, gave a timestamp inside them, and quoted
words that are actually there. A claim that fails any of those is recorded as
`grounded: false` and KEPT. Dropping it would turn the first version of a text
matcher into an uncalibrated recall filter wearing a quality criterion's
clothes — and models paraphrase, so the false-negative rate is real and, until
it is measured, unknown.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.clipper.candidate_terms import _clamp01, _num
from services.clipper.segmentation import norm_token

EVIDENCE_VERSION = "story_evidence_v1"

# What the whole clip is worth as evidence, not what it is worth as a clip.
VALID = "valid"
UNCERTAIN = "uncertain"
INVALID = "invalid"
VALIDITY: tuple[str, ...] = (VALID, UNCERTAIN, INVALID)

# Where a payoff time came from. The mechanical detector is a fallback and has
# to say so — scoring reads this to know which one it is looking at.
SOURCE_SEMANTIC = "semantic"
SOURCE_MECHANICAL = "mechanical"

# How far from a claimed timestamp an atom may sit and still be "the one it
# meant". Anchor timestamps come from segment starts, which are coarse; this is
# the same order as the sentence snapping in candidate_proposals.
_NEAR_S = 2.5

# How many atoms either side of the one holding the claimed time may be
# searched. A STRUCTURAL bound, not a number of seconds: atoms are one
# utterance each, so "the line it named, and the one before and after" adapts
# to a stream that talks fast and one that does not. The first version reached
# +/-20 SECONDS, which on this corpus is four to eight atoms, loose enough that
# a quote could match somewhere the model never pointed at and still pass.
_NEIGHBOURS = 1

# How the claim was located. Recorded because the two are not equally strong,
# and reporting them as one number claims a check that is not being performed.
MATCHED_BY_IDS = "atom_ids"
MATCHED_BY_TIME = "timestamp"


# ── normalising ──────────────────────────────────────────────────────────────

def normalise_quote(text: Any) -> str:
    """A quote reduced to what can actually be compared.

    Per TOKEN, not per string: `norm_token` takes one word — it lowercases,
    folds Romanian diacritics onto ASCII and strips punctuation from the ENDS —
    so handing it a whole sentence leaves interior punctuation in place and
    splits nothing.

    The clipper's transcript keeps punctuation and case on purpose
    (`clipper_pipeline` transcribes with `keep_punctuation=True`, because
    sentence boundaries are the primary signal for segmentation), so this is
    not a no-op: without it, a quote fails to match for reasons that have
    nothing to do with grounding.

    What it does NOT do: `norm_token` strips punctuation from the ENDS of a
    token, so an interior apostrophe survives. Both sides go through this, so
    "Y'ALL" matches "y'all" — but a model that writes "yall" against a
    transcript that says "y'all" is recorded as ungrounded. That is a real
    false negative and it is left alone deliberately: it is a paraphrase, the
    rate of them is what Batch 3 exists to MEASURE, and stripping more
    aggressively would hide the measurement rather than improve it.
    """
    tokens = [norm_token(word) for word in str(text or "").split()]
    return " ".join(t for t in tokens if t)


def _atom_text(atom: Any) -> str:
    return normalise_quote((atom or {}).get("text") if isinstance(atom, dict) else "")


def _contains_sequence(haystack, needle) -> bool:
    """Whether `needle` appears as a contiguous run of WHOLE tokens.

    Not a substring test on the joined text. That version passed "a bla"
    against "a blast furnace" - a fragment straddling a word boundary - and
    would pass any short string sitting inside a longer word. The docstring
    promised a token sequence; this is what that means.
    """
    if not needle:
        return False
    n, m = len(haystack), len(needle)
    if m > n:
        return False
    return any(list(haystack[i:i + m]) == list(needle)
               for i in range(n - m + 1) if haystack[i] == needle[0])


def _by_id(atoms: Sequence[dict] | None) -> dict[Any, dict]:
    """Atoms keyed by `i`, the index they carry in the artifact."""
    out: dict[Any, dict] = {}
    for atom in atoms or ():
        if isinstance(atom, dict) and atom.get("i") is not None:
            out[atom["i"]] = atom
    return out


# ── grounding one claim ──────────────────────────────────────────────────────

def ground_claim(claim: Any, atoms: Sequence[dict] | None) -> dict:
    """One model claim, checked against the atoms it says it came from.

    Returns the claim with `grounded`, `atom_ids` and `why_not` filled in. Four
    conditions, all required, none of them semantic:

      1. there is a quote at all, and it survives normalisation;
      2. it is LOCATED — by `atom_ids` when the model gave any, otherwise by
         the atom its timestamp falls in plus one either side;
      3. when ids were given, the timestamp falls inside those atoms;
      4. the normalised quote appears as a contiguous run of whole TOKENS in
         their text, joined in timeline order — so a quote may straddle two
         atoms, but "a bla" does not match "a blast furnace".

    `matched_by` records which locator was used. The prompt shows each line as
    `[seconds] text` and never an atom id, so today the model can only point
    with a timestamp; reporting both paths as one number would claim a check
    that is not being performed.

    An empty normalised quote fails explicitly. It matches every string by
    definition, which would make grounding a formality.
    """
    if not isinstance(claim, dict):
        return {"grounded": False, "why_not": "not a claim"}

    out = dict(claim)
    out.setdefault("t", _num(claim.get("t"), -1.0))
    quote = normalise_quote(claim.get("quote") or claim.get("evidence"))
    named = [i for i in (claim.get("atom_ids") or []) if i is not None]
    index = _by_id(atoms)

    if not atoms:
        # Nothing to check against. Not the claim's fault, and not a pass
        # either: the legacy path has no atoms at all.
        out.update({"grounded": False, "atom_ids": named,
                    "why_not": "no atoms available"})
        return out
    if not quote:
        out.update({"grounded": False, "atom_ids": named,
                    "why_not": "no quote"})
        return out

    if named:
        missing = [i for i in named if i not in index]
        if missing:
            out.update({"grounded": False, "atom_ids": named,
                        "why_not": f"unknown atom ids: {missing[:4]}"})
            return out
        chosen = [index[i] for i in named]
    else:
        # No ids given — fall back to the atoms around the claimed time. This
        # is what makes grounding work at all before the prompt is taught to
        # return ids, and it is strictly weaker: it can only confirm that the
        # words are there, not that the model knew where they were.
        chosen = _atoms_near(atoms, _num(out["t"], -1.0))
        if not chosen:
            out.update({"grounded": False, "atom_ids": [],
                        "matched_by": MATCHED_BY_TIME,
                        "why_not": "no atoms near the claimed time"})
            return out

    chosen = sorted(chosen, key=lambda a: _num(a.get("start")))
    ids = [a.get("i") for a in chosen]
    if named:
        span_start = min(_num(a.get("start")) for a in chosen)
        span_end = max(_num(a.get("end")) for a in chosen)
        t = _num(out["t"], -1.0)
        if t >= 0 and not (span_start - _NEAR_S <= t <= span_end + _NEAR_S):
            out.update({"grounded": False, "atom_ids": ids,
                        "why_not": "timestamp outside the atoms it names"})
            return out

    how = MATCHED_BY_IDS if named else MATCHED_BY_TIME
    haystack = " ".join(_atom_text(a) for a in chosen).split()
    if not _contains_sequence(haystack, quote.split()):
        out.update({"grounded": False, "atom_ids": ids, "matched_by": how,
                    "why_not": "quote is not in those atoms"})
        return out

    out.update({"grounded": True, "atom_ids": ids, "why_not": None,
                "matched_by": how,
                "matched_t": round(min(_num(a.get("start")) for a in chosen), 3)})
    return out


def _atoms_near(atoms: Sequence[dict], t: float,
                *, neighbours: int = _NEIGHBOURS) -> list[dict]:
    """The atom holding `t`, plus `neighbours` either side, in timeline order.

    The timestamp IS the model's pointer: the prompt shows each line as
    `[seconds] text` and never an atom id, so seconds are the only handle it
    has. That makes this the PRIMARY way a claim is located, not a fallback,
    and it still asks both halves of the question - that the words exist, and
    that they are where the claim said they were.
    """
    if t < 0:
        return []
    ordered = sorted((a for a in atoms if isinstance(a, dict)),
                     key=lambda a: _num(a.get("start")))
    if not ordered:
        return []
    home = next((i for i, a in enumerate(ordered)
                 if _num(a.get("start")) - _NEAR_S <= t <= _num(a.get("end")) + _NEAR_S),
                None)
    if home is None:
        # Nothing covers it. Closest by start, so a claim just past the last
        # atom is still checked rather than silently passing.
        home = min(range(len(ordered)),
                   key=lambda i: abs(_num(ordered[i].get("start")) - t))
    return ordered[max(0, home - neighbours):home + neighbours + 1]


# ── grounding a whole anchor ─────────────────────────────────────────────────

def ground_anchor(anchor: dict, atoms: Sequence[dict] | None) -> dict:
    """Every claim in one anchor, checked. Returns a NEW anchor.

    Never drops a claim, never raises. The counts it adds are what the batch's
    gate is measured on, and a grounding pass that quietly removed its own
    failures could not be measured at all.
    """
    if not isinstance(anchor, dict):
        return {}
    out = dict(anchor)

    payoff = ground_claim(
        {"t": _num(anchor.get("payoff_t"), -1.0),
         "quote": anchor.get("payoff_quote") or anchor.get("payoff_evidence"),
         "atom_ids": anchor.get("payoff_atom_ids")},
        atoms)
    context = [ground_claim(item, atoms)
               for item in (anchor.get("required_context") or [])
               if isinstance(item, dict)]

    out["payoff_grounded"] = bool(payoff.get("grounded"))
    out["payoff_evidence"] = payoff
    out["required_context"] = context
    out["grounding"] = {
        "version": EVIDENCE_VERSION,
        "payoff": bool(payoff.get("grounded")),
        "context_total": len(context),
        "context_grounded": sum(1 for c in context if c.get("grounded")),
        "payoff_matched_by": payoff.get("matched_by"),
        # Which locator was used, so two unequal checks are never one number.
        "by_ids": sum(1 for c in [payoff, *context]
                      if c.get("matched_by") == MATCHED_BY_IDS),
        "by_timestamp": sum(1 for c in [payoff, *context]
                            if c.get("matched_by") == MATCHED_BY_TIME),
    }
    return out


# ── measuring against the FINAL span ─────────────────────────────────────────

def boundary_coverage(story: dict, start: float, end: float) -> dict:
    """Which parts of the moment the chosen window actually contains.

    The question `refine_boundaries` silently invalidated: it moved both ends
    and kept the answer computed for the old ones.
    """
    story = story or {}
    payoff_t = _num(story.get("payoff_t"), -1.0)
    reaction_end = _num(story.get("reaction_end"), -1.0)
    times = [_num(c.get("t"), -1.0) for c in (story.get("required_context") or [])
             if isinstance(c, dict)]
    times = [t for t in times if t >= 0]

    return {
        # Vacuously true with no context, which is correct: a moment that needs
        # nothing explained is covered by any window.
        "context": all(start - 0.001 <= t <= end + 0.001 for t in times),
        "payoff": payoff_t < 0 or start - 0.001 <= payoff_t <= end + 0.001,
        "reaction": reaction_end < 0 or reaction_end <= end + 0.001,
        "context_outside": sum(1 for t in times
                               if t < start - 0.001 or t > end + 0.001),
    }


def validate_story_span(story: dict, coverage: dict) -> str:
    """`valid`, `uncertain` or `invalid` for this window.

    INVALID is reserved for a window that cannot work: it does not contain the
    payoff it was cut for. UNCERTAIN covers everything we cannot show — missing
    context, nothing grounded — and it is NOT a soft reject. The distinction
    matters downstream: `invalid` may not be compensated by audio energy,
    `uncertain` only means the evidence is thin.
    """
    if not coverage.get("payoff"):
        return INVALID
    if not coverage.get("context"):
        return UNCERTAIN
    grounding = (story or {}).get("grounding") or {}
    if not grounding:
        return UNCERTAIN
    if not grounding.get("payoff"):
        return UNCERTAIN
    total = int(grounding.get("context_total") or 0)
    if total and int(grounding.get("context_grounded") or 0) < total:
        return UNCERTAIN
    return VALID


def remeasure(cand: dict, *, atoms: Sequence[dict] | None = None) -> dict:
    """Recompute every story metric against the candidate's FINAL span.

    THE ONE ENTRY POINT the boundary path calls. Idempotent: running it twice
    on an unchanged window produces the same answer, which is what makes it
    safe to call after every step that can move an edge.

    Mutates `cand["story"]` in place and returns the candidate, because that is
    what `refine_boundaries` already does with everything else it derives.
    """
    from services.clipper import story as story_mod

    story = cand.get("story")
    if not isinstance(story, dict):
        return cand

    start, end = _num(cand.get("start")), _num(cand.get("end"))
    words = list(cand.get("words") or [])

    backrefs = story_mod.resolve_backrefs(words, atoms, start) if atoms else []
    if backrefs:
        story["backrefs"] = backrefs
        unresolved = [b for b in backrefs if not b.get("resolved")]
        story["unresolved_refs"] = unresolved
    debt = story_mod.context_debt(words, story, backrefs=backrefs if atoms else None)
    if story.get("callback_debt") is not None:
        # A callback's setup is minutes or hours away and can never be in the
        # window; the charge for it does not change when an edge moves.
        debt = max(debt, 0.5 * _clamp01(_num(story.get("callback_debt"))))
    story["context_debt"] = debt
    story["hook_latency"] = story_mod.hook_latency(start, story.get("hook_t"), words)

    coverage = boundary_coverage(story, start, end)
    story["boundary_coverage"] = coverage
    story["validity"] = validate_story_span(story, coverage)
    story["measured_span"] = [round(start, 3), round(end, 3)]
    story["evidence_version"] = EVIDENCE_VERSION
    return cand


def semantic_payoff(cand: dict) -> float | None:
    """The payoff the moment was CHOSEN for, or None on the legacy path.

    Read by feature extraction so `payoff_position`, `setup_ratio` and
    `payoff_strength` describe the event the clip exists for rather than the
    loudest thing inside it. Bounded to the window: a payoff outside it is a
    coverage failure, already recorded, and using it here would put the feature
    outside 0..1 and quietly poison the score.
    """
    story = cand.get("story")
    if not isinstance(story, dict):
        return None
    t = _num(story.get("payoff_t"), -1.0)
    if t < 0:
        return None
    start, end = _num(cand.get("start")), _num(cand.get("end"))
    return t if start - 0.001 <= t <= end + 0.001 else None


def stale_metrics(cand: dict) -> bool:
    """Whether this candidate's story metrics describe a different window.

    The gate for this batch is that nothing in the corpus answers True.
    """
    story = cand.get("story")
    if not isinstance(story, dict):
        return False
    measured = story.get("measured_span")
    if not isinstance(measured, list) or len(measured) != 2:
        return True
    return (abs(_num(measured[0]) - _num(cand.get("start"))) > 0.01
            or abs(_num(measured[1]) - _num(cand.get("end"))) > 0.01)
