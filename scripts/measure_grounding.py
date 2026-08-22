"""How much of the grounding gap is the model paraphrasing, and how much is us.

`story_evidence.ground_claim` marks a claim grounded only when its quote appears
as a CONTIGUOUS run of normalised tokens in the atoms it names. A claim that
fails is kept and recorded, never dropped — but the batch's gate asks for 95%
context coverage and the corpus reports 89%, and "the model paraphrases" and
"our matcher is too strict" are two different problems with two different fixes.

So the failures are re-checked against a ladder of weaker rules:

    contiguous   what ships. every token, in order, adjacent, in the atoms
                 the claim names
    near_verbatim  the same quote, verbatim, inside a WINDOW around the time
                 the claim gives — the atoms named were wrong, the words were not
    subsequence  every token, in order, gaps allowed, inside that same window
    absent       none of the above

TWO THINGS THE FIRST VERSION GOT WRONG, and they inverted its conclusion.

It counted 111 claims where there are 47 distinct ones: several candidate
variants share one anchor's story block, so the same quote was counted up to
three times and the percentages described the duplication as much as the data.

And it searched the WHOLE stream. Measured against 200 invented six-word quotes
drawn at random from the transcript's own vocabulary: 8 passed the subsequence
test and 200 of 200 passed the bag-of-words test. A check that nothing can fail
reports zero failures, which is how the first run concluded that no claim was
unsupported. Support now means the words are there NEAR where the claim says
they are; a match three hours away is not support, and the bag-of-words rung is
gone.

A claim that fails `contiguous` but passes `subsequence` is a FALSE NEGATIVE of
the matcher: the quote is really there and a transcription hiccup or a dropped
filler word broke the run. One that only passes `bag` is a paraphrase with real
support. One that reaches `absent` is the model inventing, and no matcher can
rescue it.

    python scripts/measure_grounding.py gateslice4h

Reads only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = _ROOT / "data" / "clipper"


def _read(project_id: str, name: str):
    path = DATA / project_id / "analysis" / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None


def _is_subsequence(needle: list[str], hay: list[str]) -> bool:
    it = iter(hay)
    return all(tok in it for tok in needle)


#: How far from a claim's own timestamp its words may sit and still count as
#: support. Generous — the point is to separate "we looked in the wrong atoms"
#: from "the words are not there" — but bounded, because a quote that matches
#: an hour away is a coincidence, not evidence.
WINDOW_S = 120.0


def _window_text(atoms, t: float, reach: float = WINDOW_S) -> str:
    from services.clipper import story_evidence as se

    lo, hi = t - reach, t + reach
    return se.normalise_quote(" ".join(
        str(a.get("text") or "") for a in atoms
        if isinstance(a, dict)
        and float(a.get("end") or 0) >= lo and float(a.get("start") or 0) <= hi))


def measure(project_id: str) -> dict:
    from services.clipper import story_evidence as se

    atoms = _read(project_id, "atoms") or []
    cands = _read(project_id, "candidates") or []
    if isinstance(cands, dict):
        cands = cands.get("candidates") or []
    if not atoms or not cands:
        return {"project": project_id, "status": "no artefacts"}


    tally = {"contiguous": 0, "near_verbatim": 0, "subsequence": 0,
             "absent": 0, "no_quote": 0}
    examples: dict[str, list[str]] = {k: [] for k in tally}
    # DISTINCT claims. Several candidate variants share one anchor's story
    # block, so counting per candidate counts the same claim up to three times.
    seen: set[tuple] = set()
    total = 0

    for cand in cands:
        story = cand.get("story")
        if not isinstance(story, dict):
            continue
        claims = []
        payoff = story.get("payoff_evidence")
        if isinstance(payoff, dict):
            claims.append(payoff)
        claims += [c for c in (story.get("required_context") or [])
                   if isinstance(c, dict)]

        for claim in claims:
            quote = se.normalise_quote(claim.get("quote") or claim.get("evidence"))
            key = (quote, round(float(claim.get("t") or -1), 1))
            if key in seen:
                continue
            seen.add(key)
            total += 1
            if not quote:
                tally["no_quote"] += 1
                continue
            if claim.get("grounded"):
                tally["contiguous"] += 1
                continue

            near = _window_text(atoms, float(claim.get("t") or 0.0))
            tokens = quote.split()
            if quote in near:
                bucket = "near_verbatim"
            elif _is_subsequence(tokens, near.split()):
                bucket = "subsequence"
            else:
                bucket = "absent"
            tally[bucket] += 1
            if len(examples[bucket]) < 3:
                examples[bucket].append(quote[:90])

    return {"project": project_id, "status": "ok", "claims": total,
            "tally": tally, "examples": examples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+")
    args = parser.parse_args()

    for pid in args.projects:
        out = measure(pid)
        print(f"\n=== {pid}")
        if out["status"] != "ok":
            print("  ", out["status"])
            continue
        total = out["claims"] or 1
        for name in ("contiguous", "near_verbatim", "subsequence", "absent",
                     "no_quote"):
            n = out["tally"][name]
            print(f"  {name:12s} {n:4d}  {n / total:6.1%}")
        t = out["tally"]
        strict = t["contiguous"]
        print("")
        print(f"  grounded as shipped : {strict}/{total} = {strict / total:.1%}")
        print(f"  wrong atoms named   : {t['near_verbatim']:4d}  "
              f"verbatim within {WINDOW_S:.0f}s of the claim, in other atoms")
        print(f"  matcher too strict  : {t['subsequence']:4d}  "
              "in order with gaps, in that window")
        print(f"  model invented it   : {t['absent']:4d}")
        for name in ("near_verbatim", "subsequence", "absent"):
            for ex in out["examples"][name]:
                print(f"    [{name}] {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
