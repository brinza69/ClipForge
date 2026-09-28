"""Scaling a measured series onto 0..1, once.

Extracted when Batch R3b needed the planner's normalisation at the sample rate
and copied it instead. The copy was equivalent on the day it was written, which
is exactly what made it dangerous: the same move produced the `_num` drift in
R2, where a duplicated helper quietly lost its finite check and a NaN start time
stopped matching any stretch.

The POPULATION may differ — `dynamic_edit` scales per-shot means, `dynamic_regimes`
scales per-sample values — and that difference is real and declared at each call
site. The ALGORITHM may not.
"""

from __future__ import annotations

from typing import Sequence

from services.clipper.candidate_terms import _num

__all__ = ["percentile", "scale", "normalise"]


def percentile(ordered: Sequence[float], q: float) -> float:
    """The q-th value of an ALREADY SORTED series. 0.0 when there is none."""
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(q * (len(ordered) - 1))))
    return float(ordered[idx])


def scale(value: float, lo: float, hi: float) -> float:
    """One value onto 0..1 between two bounds.

    A collapsed range returns 0.5, not 0 or 1: with no spread there is no
    evidence either way, and both extremes would be a claim.
    """
    if hi - lo <= 1e-9:
        return 0.5
    return min(1.0, max(0.0, (value - lo) / (hi - lo)))


def normalise(values: Sequence[float], *, low: float = 0.10,
              high: float = 0.90) -> tuple[list[float], bool]:
    """`(scaled series, whether it had any spread)`.

    The flag matters more than it looks. A flat series scales to 0.5 everywhere,
    and 0.5 sits under a default cut-off of 0.6 — so "no evidence either way"
    silently reads as "below the threshold" unless the caller can tell the two
    apart. It cannot infer that from the numbers alone, so it is returned.
    """
    clean = [_num(v) for v in (values or [])]
    if not clean:
        return [], False
    ordered = sorted(clean)
    lo, hi = percentile(ordered, low), percentile(ordered, high)
    if hi - lo <= 1e-9:
        return [0.5] * len(clean), False
    return [scale(v, lo, hi) for v in clean], True
