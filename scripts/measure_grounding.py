"""How much of the grounding gap is the model paraphrasing, and how much is us.

`story_evidence.ground_claim` marks a claim grounded only when its quote appears
as a CONTIGUOUS run of normalised tokens in the atoms it names. A claim that
fails is kept and recorded, never dropped — but the batch's gate asks for 95%
context coverage and the corpus reports 89%, and "the model paraphrases" and
"our matcher is too strict" are two different problems with two different fixes.

So the failures are re-checked against a ladder of weaker rules:

    contiguous   what ships. every token, in order, adjacent, in the atoms
                 the claim names
    wrong_atoms  the same quote, verbatim, somewhere ELSE in the source
    subsequence  every token, in order, gaps allowed
    bag 80% / 60%  most of the words are there, order ignored
    absent       the words are not in the source at all

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


def _bag_overlap(needle: list[str], hay: set[str]) -> float:
    if not needle:
        return 0.0
    return sum(1 for t in needle if t in hay) / len(needle)


def measure(project_id: str) -> dict:
    from services.clipper import story_evidence as se

    atoms = _read(project_id, "atoms") or []
    cands = _read(project_id, "candidates") or []
    if isinstance(cands, dict):
        cands = cands.get("candidates") or []
    if not atoms or not cands:
        return {"project": project_id, "status": "no artefacts"}

    # The whole source as normalised tokens, once. The per-claim check uses the
    # atoms a claim NAMES; this is the wider question — are the words anywhere.
    whole = se.normalise_quote(" ".join(str(a.get("text") or "") for a in atoms))
    whole_tokens = whole.split()
    whole_set = set(whole_tokens)

    tally = {"contiguous": 0, "wrong_atoms": 0, "subsequence": 0, "bag80": 0,
             "bag60": 0, "absent": 0, "no_quote": 0}
    examples: dict[str, list[str]] = {k: [] for k in tally}
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
            total += 1
            quote = se.normalise_quote(claim.get("quote") or claim.get("evidence"))
            if not quote:
                tally["no_quote"] += 1
                continue
            if claim.get("grounded"):
                tally["contiguous"] += 1
                continue
            tokens = quote.split()
            if quote in whole:
                # The words ARE in the source, verbatim — just not in the atoms
                # this claim was checked against. That is an atom-SELECTION
                # failure, not a quote failure, and it has a different fix.
                bucket = "wrong_atoms"
            elif _is_subsequence(tokens, whole_tokens):
                bucket = "subsequence"
            elif _bag_overlap(tokens, whole_set) >= 0.8:
                bucket = "bag80"
            elif _bag_overlap(tokens, whole_set) >= 0.6:
                bucket = "bag60"
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
        for name in ("contiguous", "wrong_atoms", "subsequence", "bag80",
                     "bag60", "absent", "no_quote"):
            n = out["tally"][name]
            print(f"  {name:12s} {n:4d}  {n / total:6.1%}")
        t = out["tally"]
        strict = t["contiguous"]
        print("")
        print(f"  grounded as shipped : {strict}/{total} = {strict / total:.1%}")
        print(f"  wrong atoms named   : {t['wrong_atoms']:4d}  "
              "the quote is verbatim in the source, just elsewhere")
        print(f"  matcher too strict  : {t['subsequence'] + t['bag80']:4d}  "
              "subsequence or near match")
        print(f"  model invented it   : {t['absent']:4d}")
        for name in ("wrong_atoms", "subsequence", "bag80", "absent"):
            for ex in out["examples"][name]:
                print(f"    [{name}] {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
