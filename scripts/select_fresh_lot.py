"""Choose the frames that will verify a candidate — BEFORE anyone looks at them.

    python scripts/select_fresh_lot.py pilotf81b b23c14c41495

WHY IT IS A SCRIPT AND NOT A JUDGEMENT. A hold-out chosen after the results are
known is not a hold-out, and neither is one chosen by an agent who has seen the
frames. The rule below was agreed with Codex before this ran, it looks at no
pixel, and it writes the selection to disk together with the identity of the
candidate it will judge — so a later reader can see that the list came first.

THE RULE, fixed in advance:

  * each phase's window is split into FOUR equal half-open intervals
  * inside each, the eligible frames are sorted by index and the ones at
    zero-based positions floor(n/4), floor(n/2), floor(3n/4) are taken
  * with n < 3 every available frame is taken and the DEFICIT IS REPORTED —
    never made up from another quarter, because a lot topped up from wherever
    frames were easy to find is a lot chosen for convenience

ELIGIBLE means: inside the clip, and not already looked at. That last part is
wider than "not in the two annotation tables". Codex: "Absența unui cadru din
WATCH sau HOLDOUT nu dovedește că este proaspăt." The transition windows were
inspected to place the boundaries, and three more frames were read to re-derive
those boundaries after the timing fix; all of them are excluded and named.

WHAT IT DOES NOT DO. It does not weight the last phase toward the withdrawal.
The quarters exist to stop the lot drifting toward convenient moments, and the
withdrawal needs the opposite thing — every frame from its start to the end of
the clip, which `withdrawal` reports separately with the provenance of each.
Those two answer different questions and are kept apart.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

#: Half-width of the transition windows already inspected, in seconds.
TRANSITION_S = 0.5

#: Frames read to re-derive the three boundaries after the `POS_MSEC` fix. They
#: are not in either annotation table and they are NOT fresh.
BOUNDARY_BASIS: tuple[int, ...] = (2242, 2309, 2394)

#: Where the withdrawal begins, on the source clock. The last phase's own start
#: is not it — the watch is held still to the lens for most of that phase — and
#: neither is a single number: at f2419 (241.90) it is still held, at f2424
#: (242.40) it is already leaving to the left, and nothing in between has been
#: looked at. So this is the last frame OBSERVED static, and the inspection
#: covers everything after it. 242.30 was the first value here, taken from a
#: note written under the old time labels; it would have skipped f2420..f2422,
#: which is exactly the interval the movement may start in.
WITHDRAWAL_FROM = 242.00


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _quarters(t0: float, t1: float) -> list[tuple[float, float]]:
    step = (t1 - t0) / 4.0
    return [(t0 + i * step, t0 + (i + 1) * step) for i in range(4)]


def _pick(eligible: list[int]) -> list[int]:
    """floor(n/4), floor(n/2), floor(3n/4), zero-based, on the sorted list."""
    n = len(eligible)
    if n < 3:
        return list(eligible)
    seen: list[int] = []
    for k in (n // 4, n // 2, (3 * n) // 4):
        f = eligible[k]
        if f not in seen:
            seen.append(f)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("clip")
    args = ap.parse_args()

    wa = _module("wa", _ROOT / "scripts" / "watch_annotations.py")
    bpr = _module("bpr", _ROOT / "scripts" / "build_phase_regions.py")

    phases = bpr.PHASES.get(args.clip)
    if not phases:
        print(f"REFUSED: no phase table for {args.clip}")
        return 2
    regions_path = (DATA / args.project / "phase_regions"
                    / f"{args.clip}.regions.json")
    if not regions_path.exists():
        # A lot chosen without naming the candidate it judges could later be
        # attached to any candidate at all, which is the whole failure this
        # file exists to make impossible.
        print(f"REFUSED: no frozen candidate at {regions_path}")
        return 2
    frozen = json.loads(regions_path.read_text(encoding="utf-8"))

    fps = bpr.PROXY_FPS
    first = int(bpr.CLIP_START * fps) + 1
    last = int(bpr.CLIP_END * fps)
    if last / fps >= bpr.CLIP_END:
        last -= 1

    # --- what is already looked at, and why --------------------------------
    used: dict[int, str] = {}
    for f in wa.WATCH:
        used[f] = "construction"
    for f in wa.HOLDOUT:
        used.setdefault(f, "previous_holdout")
    for name, t0, t1, _, _ in phases[1:]:
        edge = float(t0)
        for f in range(first, last + 1):
            if abs(f / fps - edge) <= TRANSITION_S:
                used.setdefault(f, f"transition_window_at_{edge}")
    for f in BOUNDARY_BASIS:
        used.setdefault(f, "read_to_re_derive_a_boundary")

    out: dict[str, Any] = {
        "schema": "clipper_fresh_lot_v1",
        "clip": args.clip,
        "candidate": {"regions": {p["phase"]: p.get("region")
                                  for p in frozen["phases"]},
                      "boundaries": [[p["phase"], p["t0"], p["t1"]]
                                     for p in frozen["phases"]],
                      "src_w": frozen["src_w"], "src_h": frozen["src_h"]},
        "rule": ("four equal half-open intervals per phase; zero-based "
                 "positions floor(n/4), floor(n/2), floor(3n/4) of the sorted "
                 "eligible frames; n < 3 takes all and reports the deficit"),
        "clip_frames": [first, last],
        "excluded": {str(f): why for f, why in sorted(used.items())},
        "phases": [], "selected": [], "deficits": [], "withdrawal": {},
    }

    print(f"{args.clip}  clip frames f{first}..f{last} at {fps} fps, "
          f"{len(used)} already looked at")
    for name, t0, t1, _, _ in phases:
        if name == "speech":
            continue
        rows = []
        for i, (a, b) in enumerate(_quarters(float(t0), float(t1))):
            pool = [f for f in range(first, last + 1)
                    if a <= f / fps < b and f not in used]
            take = _pick(pool)
            short = max(0, 3 - len(take))
            rows.append({"quarter": i, "t0": round(a, 3), "t1": round(b, 3),
                         "eligible": len(pool), "picked": take,
                         "deficit": short})
            out["selected"].extend(take)
            if short:
                out["deficits"].append(
                    {"phase": name, "quarter": i, "t0": round(a, 3),
                     "t1": round(b, 3), "eligible": len(pool),
                     "picked": take, "short_by": short})
            print(f"  {name:<11} q{i} [{a:7.3f}, {b:7.3f})  "
                  f"{len(pool):>3} eligible -> {take}"
                  + (f"   SHORT BY {short}" if short else ""))
        out["phases"].append({"phase": name, "t0": t0, "t1": t1,
                              "quarters": rows})

    # --- the withdrawal, inspected whole -----------------------------------
    # Not part of the quarters and not weighted into them: this asks whether the
    # movement is followed to the end of the clip, which a sample cannot answer.
    tail = [f for f in range(first, last + 1) if f / fps >= WITHDRAWAL_FROM]
    out["withdrawal"] = {
        "from": WITHDRAWAL_FROM, "frames": tail,
        "provenance": {str(f): (used.get(f) or ("fresh_selected"
                                                if f in out["selected"]
                                                else "fresh_unselected"))
                       for f in tail},
    }
    fresh_tail = [f for f in tail if f not in used]
    print(f"  withdrawal from {WITHDRAWAL_FROM}s: {len(tail)} frame(s) "
          f"{tail}, {len(fresh_tail)} of them fresh")

    dest = DATA / args.project / "phase_regions" / f"{args.clip}.fresh_lot.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")

    # THE DENOMINATOR BEFORE THE COUNT, and the deficit is not a rounding note:
    # a quarter that could not be filled is a hole in the coverage of the
    # verification, and it must not be reported as a smaller lot that passed.
    want = 3 * 4 * len(out["phases"])
    print(f"\n  {len(out['selected'])} of {want} frames selected, "
          f"{len(out['deficits'])} quarter(s) short")
    for d in out["deficits"]:
        print(f"    {d['phase']} q{d['quarter']} [{d['t0']}, {d['t1']}): only "
              f"{d['eligible']} eligible, short by {d['short_by']}")
    print(f"  written to {dest}")
    # A short lot is not a failure to be fixed by taking frames from elsewhere;
    # it is a fact about what the corpus has left, and the exit code says so.
    return 0 if not out["deficits"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
