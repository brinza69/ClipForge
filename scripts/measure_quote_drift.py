"""Batch S7: how far a claim's timestamp is from where its words actually are.

    python scripts/measure_quote_drift.py gateslice4h gate2d3375 pilotf81b \\
        pilotee0e pilot6b38 pilot2c8a

`measure_grounding.py` says HOW MANY claims bind and classifies the misses. This
says WHERE the words are, for every claim, using `quote_resolver` — the same
normalisation and the same whole-token rule that ships.

WHAT THE ANSWER IS FOR. `ground_claim` searches the claimed atom plus one
neighbour either side, roughly eighteen seconds, and `_NEIGHBOURS = 1` is a
constant nobody derived. The obvious move is to raise it until the numbers
improve, which is choosing a threshold to fit an answer. This prints the drift
distribution instead, so a window can be derived from it — or rejected, if the
misses turn out not to cluster at all.

READ `ambiguous` BEFORE READING ANYTHING ELSE. A quote found several times in
the transcript cannot be located by this method at all, and a large ambiguous
share would mean the drift figures describe only the easy claims. It is printed
first for that reason.

THE RUN FAILS on a project with no artefacts and on an empty corpus. It does not
fail on drift: there is no threshold here, which is the entire point.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: What `ground_claim` can currently see: the claimed atom plus one neighbour
#: either side, with atoms at a 6.2s median. Printed as a reference line, never
#: applied — a claim is not re-bound here.
SHIPPED_REACH_S = 9.3


def _read(project_id: str, name: str):
    path = DATA / project_id / "analysis" / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _claims(cands) -> list[dict]:
    """Every DISTINCT claim. Several variants share one anchor's story block,
    so counting per candidate counts the same claim up to three times."""
    from services.clipper import story_evidence as se

    out, seen = [], set()
    for cand in cands or []:
        story = cand.get("story") if isinstance(cand, dict) else None
        if not isinstance(story, dict):
            continue
        found = []
        payoff = story.get("payoff_evidence")
        if isinstance(payoff, dict):
            found.append(payoff)
        found += [c for c in (story.get("required_context") or [])
                  if isinstance(c, dict)]
        for claim in found:
            quote = se.normalise_quote(claim.get("quote") or claim.get("evidence"))
            key = (quote, round(float(claim.get("t") or -1), 1))
            if quote and key not in seen:
                seen.add(key)
                out.append(claim)
    return out


def measure(project_id: str) -> dict:
    from services.clipper import quote_resolver as qr

    atoms = _read(project_id, "atoms") or []
    cands = _read(project_id, "candidates") or []
    if isinstance(cands, dict):
        cands = cands.get("candidates") or []
    if not atoms or not cands:
        return {"project": project_id, "status": "no artefacts"}

    states: dict[str, int] = {s: 0 for s in qr.STATES}
    drifts: list[float] = []
    grounded_already = 0
    for claim in _claims(cands):
        got = qr.resolve(claim.get("quote") or claim.get("evidence"), atoms,
                         claimed_t=claim.get("t"))
        states[got["state"]] += 1
        if claim.get("grounded"):
            grounded_already += 1
        if got["state"] == qr.BOUND and got["drift_s"] is not None:
            drifts.append(got["drift_s"])
    return {"project": project_id, "status": "ok", "states": states,
            "drifts": drifts, "grounded_already": grounded_already,
            "claims": sum(states.values())}


def _spread(drifts: list[float]) -> str:
    if not drifts:
        return "no bound claims with a claimed time"
    absolute = sorted(abs(d) for d in drifts)
    p = lambda q: absolute[min(len(absolute) - 1, int(q * len(absolute)))]  # noqa: E731
    return (f"|drift| median {statistics.median(absolute):6.1f}s  "
            f"p75 {p(0.75):6.1f}s  p90 {p(0.90):6.1f}s  max {absolute[-1]:7.1f}s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("projects", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from services.clipper import quote_resolver as qr

    rows = [measure(p) for p in args.projects]
    bad = [r["project"] for r in rows if r["status"] != "ok"]
    ok = [r for r in rows if r["status"] == "ok"]

    if args.json:
        print(json.dumps({"projects": rows, "failures": bad}, indent=1))
    else:
        for row in ok:
            total = row["claims"] or 1
            print(f"=== {row['project']}  {row['claims']} claims")
            for state in qr.STATES:
                n = row["states"][state]
                print(f"  {state:13} {n:4}  {100 * n / total:5.1f}%")
            print(f"  {_spread(row['drifts'])}")
            print()

        every = [d for r in ok for d in r["drifts"]]
        pooled = {s: sum(r["states"][s] for r in ok) for s in qr.STATES}
        total = sum(pooled.values()) or 1
        print(f"POOLED  {total} claims over {len(ok)} sources")
        for state in qr.STATES:
            print(f"  {state:13} {pooled[state]:4}  "
                  f"{100 * pooled[state] / total:5.1f}%")
        print(f"  {_spread(every)}")
        print()
        # THE ONE NUMBER THIS RUN EXISTS FOR. Not applied, not a proposal — how
        # many of the located claims sit inside the reach the shipped rule
        # already has, and how many would need a wider one.
        inside = sum(1 for d in every if abs(d) <= SHIPPED_REACH_S)
        print(f"of {len(every)} located claims, {inside} are already within the "
              f"~{SHIPPED_REACH_S:.1f}s the shipped rule reaches")
        print(f"and {len(every) - inside} are not — that difference is what a "
              f"window change would have to be justified by")
        print()
        print("nothing here is applied: no claim was re-bound, no window changed")

    for name in bad:
        print(f"FAIL: {name}: no artefacts")
    if not ok:
        print("FAIL: nothing measured — a pass over nothing is not a pass")
    return 1 if bad or not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
