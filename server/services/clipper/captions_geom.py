"""The caption box, the positions it may take, and the scan between them.

Split out of `captions.py`, which was over the 500-line limit, and split HERE
rather than anywhere else because this is the part a second reader needs.
`caption_choice` reports which positions the search considered and which it
rejected, and its first attempt rebuilt the grid from `resolve_position`'s
ROUNDED return values with `int(round(...))` where the search uses `int(...)`.
Two small differences, one wrong answer: the report listed a candidate at 0.7542
against a real upper bound of 0.75 — a position the search never evaluated and
could not have chosen.

So there is one grid, and both the search and the report ask for it.
"""

from __future__ import annotations

from services.captioner_presets import (
    SAFE_CAPTION_BOTTOM,
    SAFE_HOOK_MID_Y,
    SAFE_TOP,
)

__all__ = ["CAPTION_BOX_H_PCT", "CAPTION_BOX_W_PCT",
           "CLIPPER_CAPTION_CENTER_PCT", "SCAN_STEP_PCT", "SPEC_BAND_LO",
           "SPEC_BAND_HI", "scan_bounds", "scan_grid"]

# The caption block as a fraction of the 1920-tall frame: two lines of a ~72px
# font with leading and outline. Used only for keep-out collision maths, so an
# approximation that errs large is the safe direction.
CAPTION_BOX_H_PCT = 0.10
CAPTION_BOX_W_PCT = 0.80

# Where "center" actually puts the block, as a fraction of frame height.
# Measured on all seven captioned references (docs/refs/): 50.0, 50.2, 51.1, 53,
# 62.5, 73.5 and 77.9% — four of seven cluster at 50-53%, and none sits as high
# as the 43.75% the shared SAFE_CAPTION_CENTER offset yields. That constant is
# deliberately left alone: services/captioner.py uses it for the ordinary export
# path, whose provenance is not these nine short-form clips.
CLIPPER_CAPTION_CENTER_PCT = 0.51


# How finely `resolve_position` scans for a clear band. 1% of frame height is
# ~19px at 1920 — finer than any keep-out rect's edge is meaningful, and 60-odd
# evaluations of a handful of rectangles costs nothing.
#
# This replaced a ladder of six ±4% nudges. The ladder had two faults a scan
# does not: 4% is wider than a gap can be, so it could step over a clear slot
# and land on the far side still covered; and it accepted the FIRST y that
# scored zero, which can be a pixel from a rect's edge.
SCAN_STEP_PCT = 0.01

# The style spec's clamp, applied only when something has to be avoided:
# "clamped to 55-75% of frame height". It is a guard rail on the band search,
# not a claim about where captions look best — three of the spec's own seven
# reference measurements (50.0, 50.2, 51.1) sit below it, and an unobstructed
# caption is left at its preset position for that reason.
SPEC_BAND_LO, SPEC_BAND_HI = 0.55, 0.75


def scan_grid(lo: float, hi: float) -> list[float]:
    """Every position the band search evaluates, low to high.

    `int()` and not `int(round())`: the last step is taken only if it FITS.
    With `lo` at 0.15416... and `hi` at 0.75 the range is 59.58 steps, so the
    grid ends at 0.7442 and there is no 60th step. Rounding the count up put a
    candidate above `hi` into a report about what the search chose from.
    """
    return [round(lo + i * SCAN_STEP_PCT, 4)
            for i in range(int((hi - lo) / SCAN_STEP_PCT) + 1)]


def _widest_band(clear: list[float]) -> float | None:
    """Centre of the longest run of consecutive clear positions."""
    if not clear:
        return None
    best = run = [clear[0]]
    for y in clear[1:]:
        if y - run[-1] <= SCAN_STEP_PCT * 1.5:
            run.append(y)
        else:
            run = [y]
        if len(run) > len(best):
            best = run
    return (best[0] + best[-1]) / 2.0


def _base_y_pct(position: str, out_h: int) -> float:
    half = CAPTION_BOX_H_PCT / 2
    pos = (position or "bottom").strip().lower()
    if pos in ("top", "upper"):
        return (SAFE_TOP / out_h) + half
    if pos in ("center", "middle", "mid"):
        return CLIPPER_CAPTION_CENTER_PCT
    if pos in ("hook", "mid_high"):
        return SAFE_HOOK_MID_Y / out_h
    return (out_h - SAFE_CAPTION_BOTTOM) / out_h


def scan_bounds(out_h: int) -> tuple[float, float]:
    """`(lo, hi)` of the band `resolve_position` may place a caption in.

    EXTRACTED, not duplicated. `caption_choice` needs these to report which
    positions were considered, and its first attempt reconstructed them from
    `resolve_position`'s ROUNDED return values — which put one grid point at
    0.7542 against a real `hi` of 0.75, so the report named a candidate the
    search had never evaluated and could not have chosen.
    """
    lo = (SAFE_TOP / out_h) + CAPTION_BOX_H_PCT / 2
    hi = (out_h - SAFE_CAPTION_BOTTOM) / out_h
    if lo > hi:  # pathological output size — keep the band non-empty
        lo = hi
    return lo, hi
