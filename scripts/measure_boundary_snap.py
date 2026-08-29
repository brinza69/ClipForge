"""Batch R5a: what the final-end snap moves, before anybody re-scores anything.

`_fit` now snaps the end off a word, which it had always claimed to do and only
ever did for the start. This measures the consequence on the CORPUS AS STORED —
it applies the same `_snap` to each recorded window's end and reports where the
cut goes, how far it moves, and whether it can be made without breaching the
minimum, the maximum or the media.

    python scripts/measure_boundary_snap.py --all

IT NAMES THE RISKIEST MOVES, not just their distribution. A move of five
seconds and a move of fifty milliseconds are both "the end left the word", and
only one of them is worth a human looking. Each named move carries the token,
its duration, where that duration sits in THIS transcript's own distribution,
and the transcriber's probability for it — the only evidence the transcript
itself offers about whether the timestamp can be trusted.

NO THRESHOLD IS APPLIED IN `_fit`, and the measurement is why. On the corpus,
p99 of token duration flags 19 of the 261 moves and p99.9 flags exactly the
three extremes — so picking p99.9 would be choosing the cut-off after seeing
which answer it gives. The audio does not settle it either: all three extremes
sit in intervals classified as speech with the RMS active, so "it adds seconds
of silence" is not something anybody has shown. A statistical rule can say the
timestamp is odd; it cannot say where the word really ends.

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


#: How many of the largest moves to name per project, and how large a move has
#: to be to be worth naming. Reporting thresholds, not decision thresholds —
#: nothing here changes what `_fit` does.
TOP_N = 5
NOTABLE_S = 1.0


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
           "truncated_after": 0, "moved": 0, "shifts": [], "moves": [],
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
            token = bc._straddled(words, end) or {}
            out["moves"].append({
                "shift": round(snapped - end, 3),
                "at": round(end, 3),
                "word": str(token.get("word") or ""),
                # The token's own duration and the transcriber's confidence in
                # it. A five-second "word" is a timestamp nobody should trust,
                # and the probability is the only evidence the transcript itself
                # offers about that.
                "token_s": round(_num(token.get("end"))
                                 - _num(token.get("start")), 3),
                "probability": token.get("probability"),
            })
    # The token-duration percentile of each move, from THIS transcript rather
    # than from a threshold: p99 flags 19 of the corpus's 261 and p99.9 flags
    # exactly the three extremes, which is why no cut-off is applied in `_fit` —
    # choosing it after seeing the answer would be calibrating on the gate.
    lengths = sorted(_num(w.get("end")) - _num(w.get("start")) for w in words)
    for move in out["moves"]:
        below = sum(1 for L in lengths if L <= move["token_s"])
        move["token_pct"] = round(100.0 * below / max(1, len(lengths)), 2)
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
    for move in sorted(row["moves"], key=lambda m: -abs(m["shift"]))[:TOP_N]:
        if abs(move["shift"]) < NOTABLE_S:
            break
        prob = move["probability"]
        print(f"{'':14} +{move['shift']}s at {move['at']}s on {move['word']!r} "
              f"(token {move['token_s']}s, p{move['token_pct']} of this "
              f"transcript, whisper "
              f"{'unavailable' if prob is None else round(float(prob), 3)})")


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
