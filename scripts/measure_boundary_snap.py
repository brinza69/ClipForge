"""Batch R5a: what the final-end snap moves, before anybody re-scores anything.

`_fit` now snaps the end off a word, which it had always claimed to do and only
ever did for the start. This measures the consequence on the CORPUS AS STORED —
it applies the same `_snap` to each recorded window's end and reports where the
cut goes, how far it moves, and whether it can be made without breaching the
minimum, the maximum or the media.

    python scripts/measure_boundary_snap.py --all

WHAT THIS IS AND IS NOT. It measures the MOVE, not the consequence of the move.
A window whose end shifts 0.3s gets different boundary features, a different
score, possibly a different place in the dedupe group and possibly a different
board. None of that is visible here, and none of it can be without re-scoring on
a clone — which is the next step, not this one. Reading a clean report here as
"the fix is safe" would be the same mistake as reading a truncated report as a
corpus figure.

WHY THE STORED WINDOWS ARE STILL WORTH MEASURING. They are the inputs the defect
was found in, and the snap is a pure function of a time and a word list. What
it does to them is exactly what it will do to the same windows on a re-score;
what changes downstream is what a re-score is for.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "server"))

DATA = Path(os.environ.get("CLIPFORGE_DATA_DIR") or (_ROOT / "data")) / "clipper"

from config import settings  # noqa: E402
from database import async_session  # noqa: E402
from models import ProjectModel, TranscriptModel  # noqa: E402
from sqlalchemy import select  # noqa: E402

from services.clipper import boundary_completion as bc  # noqa: E402
from services.clipper.candidate_terms import _num, _snap, _words_for  # noqa: E402


def _percentile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    return round(ordered[min(len(ordered) - 1,
                             max(0, int(q * (len(ordered) - 1))))], 3)


async def _load(project_id: str):
    async with async_session() as session:
        row = (await session.execute(
            select(TranscriptModel)
            .where(TranscriptModel.project_id == project_id).limit(1)
        )).scalar_one_or_none()
        project = await session.get(ProjectModel, project_id)
    if not row or not row.segments:
        return None, 0.0, 0.0, 0.0
    cfg = (project.clipper_settings if project else None) or {}
    return ({"language": row.language, "segments": row.segments},
            float(cfg.get("min_clip_s") or settings.clipper_min_clip_s),
            float(cfg.get("max_clip_s") or settings.clipper_max_clip_s),
            float((project.duration if project else 0) or 0.0))


def _measure(project_id: str) -> dict | None:
    path = DATA / project_id / "analysis" / "candidates.json"
    if not path.exists():
        return None
    rows = [c for c in json.loads(path.read_text(encoding="utf-8"))
            if isinstance(c, dict)]
    transcript, lo, hi, duration = asyncio.run(_load(project_id))
    if not transcript:
        return {"project": project_id, "refused": "no_transcript_in_db"}
    words = _words_for({}, transcript)
    ceiling = max([duration] + [_num(w.get("end")) for w in words[-1:]]) or duration

    out = {"project": project_id, "windows": len(rows), "truncated_before": 0,
           "truncated_after": 0, "moved": 0, "shifts": [],
           "refused_min": 0, "refused_max": 0, "refused_media": 0}
    for cand in rows:
        start, end = _num(cand.get("start")), _num(cand.get("end"))
        if bc._straddled(words, end) is None:
            continue
        out["truncated_before"] += 1
        limit = min(start + hi, ceiling)
        snapped = _snap(words, end, to_end=True, limit=limit)
        # The same three guards `_fit` applies, counted apart so a refusal is
        # attributable rather than merely a failure.
        if snapped > ceiling + 1e-6:
            out["refused_media"] += 1
        elif snapped - start > hi:
            out["refused_max"] += 1
        elif snapped - start < lo:
            out["refused_min"] += 1
        elif bc._straddled(words, snapped) is not None:
            out["truncated_after"] += 1
        else:
            out["moved"] += 1
            out["shifts"].append(round(snapped - end, 3))
    return out


def _report(row: dict) -> None:
    name = row["project"]
    if row.get("refused"):
        print(f"{name:14} {row['refused']}")
        return
    left = row["truncated_after"] + row["refused_min"] + row["refused_max"] \
        + row["refused_media"]
    print(f"{name:14} {row['windows']:5} windows   truncated {row['truncated_before']:4}"
          f" -> {left:4}   moved {row['moved']}")
    if row["refused_min"] or row["refused_max"] or row["refused_media"]:
        print(f"{'':14} refused: min {row['refused_min']}  max {row['refused_max']}"
              f"  media {row['refused_media']}")
    shifts = sorted(row["shifts"])
    if shifts:
        print(f"{'':14} shift: median {_percentile(shifts, 0.50)}s  "
              f"p90 {_percentile(shifts, 0.90)}s  max {round(shifts[-1], 3)}s")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    names = list(args.projects)
    if args.all or not names:
        names = sorted(p.name for p in DATA.glob("*") if p.is_dir())
    rows = [r for r in (_measure(n) for n in names) if r]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    for row in rows:
        _report(row)
    before = sum(r.get("truncated_before", 0) for r in rows)
    left = sum(r.get("truncated_after", 0) + r.get("refused_min", 0)
               + r.get("refused_max", 0) + r.get("refused_media", 0) for r in rows)
    shifts = sorted(s for r in rows for s in r.get("shifts") or [])
    print(f"\n{'POOLED':14} truncated {before} -> {left} over "
          f"{sum(r.get('windows', 0) for r in rows)} windows")
    if shifts:
        print(f"{'':14} shift: median {_percentile(shifts, 0.50)}s  "
              f"p90 {_percentile(shifts, 0.90)}s  max {round(shifts[-1], 3)}s  "
              f"(n={len(shifts)})")
    print(f"{'':14} This is the MOVE. What it does to scores, dedupe groups, "
          f"the shortlist and the board is not measured here and cannot be "
          f"without a re-score on a clone.")
    return 0 if left == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
