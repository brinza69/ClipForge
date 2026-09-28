"""Check the R1 merge against the real pilot corpus, and freeze it as a fixture.

Two jobs, one file, because they must not disagree. Run without arguments it
REPORTS what the merge does to the 58 stored plans; with `--write` it also
freezes a minimal projection of those plans into the test fixture, so the gate
survives on a machine that has never seen `data/clipper`.

    python scripts/build_shot_merge_fixture.py
    python scripts/build_shot_merge_fixture.py --write

Why a projection and not the plans themselves: the sidecars are private, mutable
and about to change. A correct R1 re-render rewrites them with the merged shot
list, so a test reading them directly would assert 1.341 -> 1.225 against files
that already say 1.225 — and fail for being right. The fixture is the corpus AS
IT WAS when the rule was measured, and it is versioned.

The projection keeps only what the geometry actually reads: `composition`, the
times, the rect HEIGHT (`_size_timeline` uses nothing else), the anchor, `move`,
`snap` and `shake`, plus the plan's style and source size.

Reads the corpus. Writes only the fixture, only when asked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"
FIXTURE = _ROOT / "server" / "tests" / "fixtures" / "pilot_shot_plans.json"
PILOTS = ("pilotf81b", "pilotee0e", "pilot6b38", "pilot2c8a")

from services.clipper import dynamic_geometry as geo  # noqa: E402


def _project(plan: dict) -> dict:
    """The smallest plan the merge cannot tell from the original."""
    shots = []
    for shot in plan.get("shots") or []:
        rect = shot.get("rect") or {}
        shots.append({
            "t0": shot.get("t0"), "t1": shot.get("t1"),
            "composition": shot.get("composition"),
            "rect": {"h": rect.get("h")},
            "anchor": shot.get("anchor"),
            "move": shot.get("move"), "snap": shot.get("snap"),
            "shake": shot.get("shake"),
        })
    return {
        "src_w": plan.get("src_w"), "src_h": plan.get("src_h"),
        "style": plan.get("style") or {},
        "shots": shots,
    }


def _plans() -> list[dict]:
    out = []
    for project in PILOTS:
        for path in sorted((DATA / project / "exports").glob("*.json")):
            sidecar = json.loads(path.read_text(encoding="utf-8"))
            if sidecar.get("dynamic_plan"):
                out.append(sidecar["dynamic_plan"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help=f"freeze the projection into {FIXTURE.name}")
    args = ap.parse_args()

    plans = _plans()
    if not plans:
        print(f"no pilot plans under {DATA} — nothing to check")
        return 1

    before = after = 0
    for plan in plans:
        src_w = int(plan.get("src_w") or 1920)
        src_h = int(plan.get("src_h") or 1080)
        merged = geo.merge_equivalent_shots(plan, src_w, src_h)
        before += len(plan["shots"])
        after += len(merged["shots"])
    print(f"plans: {len(plans)}")
    print(f"shots: {before} -> {after}   ({before - after} invisible cuts removed)")

    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps({
            "note": "Minimal projection of the four pilot projects' dynamic "
                    "plans, as they were when the R1 rule was measured. "
                    "Regenerate with scripts/build_shot_merge_fixture.py --write.",
            "plans": [_project(p) for p in plans],
            "shots_before": before,
            "shots_after": after,
        }, separators=(",", ":")), encoding="utf-8")
        print(f"wrote {FIXTURE} ({FIXTURE.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
