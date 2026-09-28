"""Aggregating clip reports into project and corpus figures.

Split from `edit_quality.py` when it crossed 500 lines. The seam is real:
everything in that file measures ONE export, and everything here is about what
may and may not be added up across many.

This module imports `edit_quality`; `edit_quality` must NOT import it back.
There was a re-export at the bottom of that file and it made the pair a cycle
that worked only if you happened to import them in the right order — importing
this module first raised ImportError. Callers that want both ask for both.

Two rules that only exist at this level, both of them things the first version
got wrong:

- **A rate is built from clips that carry both halves.** `sum(shots)` over
  `sum(durations)` read two independent lists, and a clip with shots but no
  duration next to a clip with duration but no shots produced a confident
  figure belonging to neither.
- **A total is a total, or it is `unavailable`.** One clip whose boundaries
  cannot be judged makes the sum a lower bound; reporting it as the total
  reintroduces, in the aggregate, exactly what the per-clip rule prevents. The
  lower bound is still published, under a name that says what it is.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from services.clipper.edit_quality import LEAD_IN_EPS, TAIL_TIGHT_S, UNAVAILABLE


def _fold_defects(rows: Iterable[dict]) -> dict[str, int]:
    """Every defect on every clip, counted. A clip can carry more than one."""
    out: dict[str, int] = {}
    for row in rows:
        for name in row.get("defects") or []:
            out[name] = out.get(name, 0) + 1
    return out


def _available(rows: Iterable[dict], key: str) -> list[float]:
    out = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
    return out


def _tally(rows: Iterable[dict], key: str) -> dict[str, int]:
    """Count one string per clip: render version, fingerprint status, defect."""
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        name = value if isinstance(value, str) and value else UNAVAILABLE
        out[name] = out.get(name, 0) + 1
    return out


def _fold(rows: Iterable[dict], key: str) -> dict[str, int]:
    """Add up per-clip tallies of SHOTS.

    Kept apart from `_tally` on purpose. Folding both through one function let a
    clip with no shot list at all — a static export — contribute a count of one
    to a tally whose unit is shots, and the composition totals came out twelve
    above the shot count. A clip that has nothing to tally contributes nothing;
    how many did so is `coverage`.
    """
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if not isinstance(value, dict):
            continue
        for name, n in value.items():
            out[name] = out.get(name, 0) + int(n)
    return out


def _totals(rows: Sequence[dict]) -> dict:
    """The aggregate half shared by project and macro reports.

    Every rate here is POOLED — total shots over total seconds. That is not the
    average of the sources, and the two answer different questions; the macro
    report carries both under names that say which is which.
    """
    durations = _available(rows, "duration_s")
    shots = _available(rows, "shots")
    minimums = _available(rows, "min_shot_s")
    equivalent = _available(rows, "equivalent_cuts")
    trim_jumps = _available(rows, "trim_jumps")
    broken = _available(rows, "non_contiguous_boundaries")
    undecidable = _available(rows, "undecidable_boundaries")
    lead_ins = _available(rows, "lead_in_s")
    tails = _available(rows, "tail_s")

    # The rate is built ONLY from clips that carry both halves. Dividing
    # `sum(shots)` by `sum(durations)` read two independent lists: a clip with
    # shots but no duration and a clip with duration but no shots produced a
    # confident 12/min that belonged to neither of them.
    paired = [(r["shots"], r["duration_s"]) for r in rows
              if isinstance(r.get("shots"), (int, float))
              and not isinstance(r.get("shots"), bool)
              and isinstance(r.get("duration_s"), (int, float))
              and not isinstance(r.get("duration_s"), bool)]
    paired_shots = sum(n for n, _ in paired)
    paired_seconds = sum(d for _, d in paired)

    out: dict[str, Any] = {
        "clips": len(rows),
        "duration_s": sum(durations) if durations else UNAVAILABLE,
        "shots": int(sum(shots)) if shots else UNAVAILABLE,
        "shots_per_minute_pooled": (paired_shots / (paired_seconds / 60.0)
                                    if paired and paired_seconds else UNAVAILABLE),
        "min_shot_s": min(minimums) if minimums else UNAVAILABLE,
        "composition": _fold(rows, "composition") or UNAVAILABLE,
        "regime": _fold(rows, "regime") or UNAVAILABLE,
        # A total, or nothing. One clip whose boundaries cannot be judged makes
        # the sum a lower bound, and the aggregate reintroduced exactly the
        # failure the per-clip rule was written to prevent — 1 out of 2 clips
        # reported as "1 invisible cut".
        "equivalent_cuts": (int(sum(equivalent))
                            if equivalent and len(equivalent) == len(rows)
                            else UNAVAILABLE),
        "equivalent_cuts_known_lower_bound": (int(sum(equivalent)) if equivalent
                                              else UNAVAILABLE),
        "trim_jumps": (int(sum(trim_jumps))
                       if trim_jumps and len(trim_jumps) == len(rows)
                       else UNAVAILABLE),
        "trim_jumps_known_lower_bound": (int(sum(trim_jumps)) if trim_jumps
                                         else UNAVAILABLE),
        "non_contiguous_boundaries": int(sum(broken)) if broken else UNAVAILABLE,
        "undecidable_boundaries": int(sum(undecidable)) if undecidable else UNAVAILABLE,
        # Counted, not averaged. "Half the clips start on the first word" is the
        # finding; a mean lead-in of 12ms across 58 clips says nothing.
        "clips_starting_on_first_word": sum(1 for v in lead_ins if v <= LEAD_IN_EPS)
        if lead_ins else UNAVAILABLE,
        "clips_with_tight_tail": sum(1 for v in tails if v <= TAIL_TIGHT_S)
        if tails else UNAVAILABLE,
        "captions_duplicate_declared": UNAVAILABLE,
        "static_exports": sum(1 for r in rows if r.get("static_export")),
        "defects": _fold_defects(rows),
        # Which renderer produced this set, and whether each digest still
        # matches the plan beside it. A mixed or edited corpus is not comparable
        # to a baseline, and the only way to notice is to print it.
        "render_versions": _tally(rows, "render_version"),
        "fingerprints": _tally(rows, "fingerprint_status"),
        "duration_clocks": _tally(rows, "duration_clock"),
        # How many clips each figure is actually made of. Without this the
        # reader cannot tell 1.341 shots over 58 clips from 1.341 over 12.
        "coverage": {
            "composition": sum(1 for r in rows if isinstance(r.get("composition"), dict)),
            "shots_per_minute": len(paired),
            "duration_s": len(durations),
            "shots": len(shots),
            "min_shot_s": len(minimums),
            "equivalent_cuts": len(equivalent),
            "trim_jumps": len(trim_jumps),
            "non_contiguous_boundaries": len(broken),
            "lead_in_s": len(lead_ins),
            "tail_s": len(tails),
        },
    }
    return out


def project_report(project_id: str, clips: Sequence[dict]) -> dict:
    """Aggregate one project's clip reports. `clips` are `clip_report` outputs."""
    return {"project_id": project_id, **_totals(list(clips))}


def macro_report(projects: Sequence[dict], clips: Sequence[dict]) -> dict:
    """The corpus figure, aggregated from the clip reports rather than from the
    project reports.

    Summing project aggregates would compound their `unavailable` gaps: a
    project whose duration is unknown would drop its shots from the macro count
    too. Going back to the clips keeps each metric's coverage independent.

    Both rates are reported because they disagree and both are true. Pooled
    (29,3349/min on the pilots) is the corpus rate — one long tape. The source
    mean (29,5930/min) weighs each source equally, which is the one to watch
    when the question is whether the grammar travels across content types
    rather than how the biggest project behaves.
    """
    rates = _available(projects, "shots_per_minute_pooled")
    return {
        "projects": len(projects),
        **_totals(list(clips)),
        "shots_per_minute_source_mean": (sum(rates) / len(rates) if rates
                                         else UNAVAILABLE),
        "coverage_sources": len(rates),
    }
