"""Which sub-score actually decides the order of the board.

This measurement has now been done twice, three sessions apart, and both times
it found dead features nobody suspected. It is a script so the third time is
one command.

WEIGHT x STANDARD DEVIATION, over real candidates. Neither half is enough on
its own and that is the whole point:

  * a sub-score with a big spread and 2% of the profile changes nothing;
  * a sub-score with 8% of the profile and no spread changes nothing either,
    and is worse, because it looks like it is working. It is a constant offset
    dragging every score by the same amount while taking weight away from
    something that discriminates.

What it found, session 3: `audio_peak_ratio` read exactly 1.000 on all 57
windows, `audio_dynamic_range` had sd 0.027 at a median of 0.998, and
`peak_prominence` collapsed to `1 - median` — all three dividing by a value
that was pinned at 1.

What it found, session 6: `platform_fit` was the LARGEST contributor at 2.10 of
16.22 total, for answering "is the duration in band", while `payoff` — whether
the moment has a point — contributes 1.14. And `emotion` held 8% of the profile
with 76% of candidates scoring exactly 10 out of 100.

Read the total at the bottom before anything else. The ranking has about 16
points of spread on a 0-100 scale; a sub-score contributing under 0.3 is not
ordering anything.

    python scripts/score_contribution.py
    python scripts/score_contribution.py --profile podcast
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))

from services.clipper.scoring import PROFILES, SUB_SCORES    # noqa: E402

DEAD_BELOW = 0.30


def candidates() -> list[dict]:
    """Every scored candidate on disk, across projects."""
    out: list[dict] = []
    root = _REPO / "data" / "clipper"
    for project in sorted(root.glob("*/analysis/candidates.json")):
        try:
            rows = json.loads(project.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rows, list):
            out += [r for r in rows if isinstance(r, dict) and r.get("sub_scores")]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default="gaming", choices=sorted(PROFILES))
    args = ap.parse_args()

    rows = candidates()
    if not rows:
        raise SystemExit("no scored candidates on disk — run a project first")
    weights = PROFILES[args.profile]

    values: dict[str, list[float]] = {}
    for row in rows:
        for key, value in (row.get("sub_scores") or {}).items():
            try:
                values.setdefault(key, []).append(float(value))
            except (TypeError, ValueError):
                continue

    print(f"{len(rows)} candidates, profile `{args.profile}`\n")
    print(f"{'sub-score':22s} {'weight':>7s} {'median':>7s} {'sd':>7s} "
          f"{'contributes':>12s}")

    contribution: dict[str, float] = {}
    for key in SUB_SCORES:
        vals = values.get(key) or []
        if not vals:
            continue
        contribution[key] = weights.get(key, 0.0) * st.pstdev(vals)

    for key, share in sorted(contribution.items(), key=lambda kv: -kv[1]):
        vals = values[key]
        flag = "  <- decides nothing" if share < DEAD_BELOW else ""
        print(f"{key:22s} {weights.get(key, 0.0):7.1%} {st.median(vals):7.1f} "
              f"{st.pstdev(vals):7.2f} {share:12.3f}{flag}")

    total = sum(contribution.values())
    dead = [k for k, v in contribution.items() if v < DEAD_BELOW]
    print(f"\ntotal spread the ranking has to work with: {total:.2f} points")
    if dead:
        weight = sum(weights.get(k, 0.0) for k in dead)
        print(f"contributing under {DEAD_BELOW}: {', '.join(sorted(dead))}")
        print(f"  — {weight:.0%} of the profile for "
              f"{sum(contribution[k] for k in dead) / total:.0%} of the work")


if __name__ == "__main__":
    main()
