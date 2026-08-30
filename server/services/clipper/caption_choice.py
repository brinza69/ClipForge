"""WHY the caption is where it is — Batch R6, the sidecar half.

`caption_placement` says what the burned caption LANDS ON. This says how it got
there: the position that was proposed, the alternatives that were rejected, and
the reason the winner won. §R6 asks for all three in the sidecar, and none of
them exists anywhere today — `resolve_position` returns two floats and forgets
the search that produced them.

THIS CHANGES NOTHING. It reads a decision that has already shipped; it does not
make one. Every number here comes back out of `captions` itself.

AND THAT IS THE WHOLE DESIGN. The rule this file exists to obey was paid for in
Batch R5: `_snapped()` re-implemented `_fit`'s guards "just to report on them",
the copy drifted, and the audit passed clips the worker would have refused. So
nothing here recomputes a placement rule.

- THE ANSWER comes from `resolve_position`, called.
- THE PRESET POSITION comes from `resolve_position` called with an EMPTY layout:
  no keep-out rectangles means nothing to avoid, so the function returns its own
  clamped base. The base is not recomputed from `SAFE_TOP` and friends.
- THE GRID comes from `captions.scan_grid(*captions.scan_bounds(out_h))`, the
  same two calls `resolve_position` makes. The first version rebuilt it from
  `resolve_position`'s ROUNDED return values with `int(round(...))` where the
  search uses `int(...)` — two small differences and one wrong answer: the
  report listed a candidate at 0.7542 against a real upper bound of 0.75, a
  position the search never evaluated and could not have chosen. It was
  reporting on a search nobody ran, which is this file's one job to not do.
- THE COVERAGE at any position is `captions._overlap_area`, imported rather than
  restated. A private name crossing a module boundary is a smell; a second copy
  of collision arithmetic that decides where text lands is worse, and this plan
  has already paid for that trade once.

WHAT IT CANNOT EXPLAIN IT SAYS SO. `UNEXPLAINED` is in the vocabulary and it is
reachable. A reason that always finds one is not a reason — it is a label
applied after the fact, and the next reader would trust it.

A FINDING IT CARRIES RATHER THAN FIXES, and it is worse than it first looked.

`_iter_rects` silently ignores any entry without `w` and `h`, and `_norm_rect`
silently drops any rect with a non-positive width or height — so a malformed
keep-out becomes "nothing in the way" and the caption is placed as if the frame
were clear.

A NON-FINITE RECT DOES THE OPPOSITE, AND IT MOVES DELIVERED CAPTIONS. Every
comparison against a NaN is false, so `h <= 0` does not reject it, `min(by1,
nan)` returns `by1` and `max(by0, nan)` returns `by0` — the rect ends up
overlapping the caption box at EVERY position in the scan. Not inert: total.
Measured, on a layout with one real facecam rect and one NaN rect beside it:

    clean                chosen 0.3092  widest clear band, 32 of 61 clear
    with one NaN rect    chosen 0.4642  nothing is clear, least-covered won

One malformed entry erases the band search and the caption is burned 15% of the
frame lower. That is live behaviour in `captions`, changing it would move
delivered captions, and this batch does not move them — so the counts travel in
the report instead (`offered`, `used`, `unreadable`, `not_finite`) and whoever
reads the sidecar can see that the placement was decided over rectangles nobody
could read.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

from services.clipper.captions import (
    SCAN_STEP_PCT,
    SPEC_BAND_HI,
    SPEC_BAND_LO,
    _iter_rects,
    _norm_rect,
    _overlap_area,
    resolve_position,
    scan_bounds,
    scan_grid,
)

__all__ = ["REASONS", "REJECTED_BECAUSE", "REFUSALS", "explain"]

#: What this refuses to explain, and why. IT MUST NEVER RAISE: a caption is
#: burned whether or not anybody can say why, so an explanation that throws
#: takes the export down to report on it. `(layout or {}).get` raised on a list,
#: `int(out_w)` on a string, and `_base_y_pct` does `position.strip()` — so a
#: position that is a number raised from inside `resolve_position` itself.
BAD_LAYOUT = "layout_not_a_record"
BAD_POSITION = "position_not_a_name"
BAD_OUT_SIZE = "output_size_not_positive_numbers"
UNEXPLAINABLE = "the_shipping_function_refused_this_input"
REFUSALS: tuple[str, ...] = (BAD_LAYOUT, BAD_POSITION, BAD_OUT_SIZE,
                             UNEXPLAINABLE)

#: Why the winning position won, as a closed list. Each one is decided from
#: what the shipping function RETURNED plus what the coverage arithmetic says
#: about it — never by re-running its branches.
NO_RECTS = "no_keep_out_rectangles"
PRESET_CLEAR = "preset_position_is_clear"
BAND_IN_SPEC = "widest_clear_band_inside_the_spec_clamp"
BAND_OUTSIDE_SPEC = "widest_clear_band_outside_the_spec_clamp"
NOTHING_CLEAR = "nothing_is_clear_so_least_covered_won"
#: The honest one. Reachable on purpose: a vocabulary with no way to fail is a
#: set of labels, not a set of findings.
UNEXPLAINED = "chosen_position_matches_no_known_rule"
REASONS: tuple[str, ...] = (NO_RECTS, PRESET_CLEAR, BAND_IN_SPEC,
                            BAND_OUTSIDE_SPEC, NOTHING_CLEAR, UNEXPLAINED)

#: Why a clear band that existed was not taken.
NARROWER = "a_wider_clear_band_existed"
OUT_OF_SPEC = "outside_the_55_75_spec_clamp_while_one_inside_it_existed"
REJECTED_BECAUSE: tuple[str, ...] = (NARROWER, OUT_OF_SPEC)

#: Half a scan step. A band's centre is the midpoint of its run, so it lands
#: between grid points whenever the run has an even length — matching the
#: chosen position to a band therefore needs a tolerance, and half a step is
#: the largest one that cannot reach the next band.
_EPS = SCAN_STEP_PCT / 2.0


def _rect_census(zones: Any, out_w: int, out_h: int) -> dict:
    """How many keep-out rectangles were offered, and how many survived.

    COUNTING, not deciding. `_iter_rects` is asked what it yields rather than
    being re-implemented; the only thing computed here is how many values the
    container held, which is arithmetic on a list and not a rule.
    """
    offered = 0
    values: Iterable[Any] = ()
    if isinstance(zones, dict):
        values = list(zones.values())
    elif isinstance(zones, (list, tuple)):
        values = list(zones)
    for value in values:
        # One level of nesting, because that is the shape `_iter_rects` walks.
        offered += len(value) if isinstance(value, (list, tuple)) else 1

    yielded = list(_iter_rects(zones))
    used = [_norm_rect(r, out_w, out_h) for r in yielded]
    kept = [r for r in used if r is not None]
    not_finite = [r for r in kept
                  if not all(math.isfinite(v) for v in r)]
    return {
        "offered": offered,
        "recognised_as_rectangles": len(yielded),
        "used": len(kept),
        # Survived `_norm_rect` and then covers the caption box at EVERY scan
        # position, because every comparison against a NaN is false. This one
        # changes where the caption is burned; see the module docstring for the
        # measurement.
        "not_finite": len(not_finite),
        # Offered and thrown away without a word, at either gate.
        "unreadable": offered - len(kept),
    }


def _bands(grid: Sequence[float], clear: Sequence[float]) -> list[dict]:
    """Runs of consecutive clear positions, each with its centre and extent.

    These are the alternatives. `_widest_band` picks among exactly these, so
    reporting them is reporting the search's own candidate set rather than a
    parallel one invented for the report.
    """
    out: list[dict] = []
    run: list[float] = []
    for y in clear:
        if run and y - run[-1] <= SCAN_STEP_PCT * 1.5:
            run.append(y)
        else:
            if run:
                out.append(run)
            run = [y]
    if run:
        out.append(run)
    return [{"from": round(r[0], 4), "to": round(r[-1], 4),
             "steps": len(r), "centre": round((r[0] + r[-1]) / 2.0, 4),
             "in_spec": SPEC_BAND_LO <= (r[0] + r[-1]) / 2.0 <= SPEC_BAND_HI}
            for r in out]


def _why_rejected(band: dict, *, any_in_spec: bool, widest: int) -> str | None:
    """Why a clear band that existed was not the one taken, or None.

    THE ORDER MATTERS: a band outside the clamp loses to the clamp first,
    whatever its width, because that is the order the search applies them in.

    None is a real answer and not a gap. A band as wide as the winner, under the
    same clamp, lost to nothing this module can name — `_widest_band` breaks
    that tie by scan order, which is an implementation detail and not a reason.
    Calling it `NARROWER` would be a guess dressed as a finding.
    """
    if any_in_spec and not band["in_spec"]:
        return OUT_OF_SPEC
    if band["steps"] < widest:
        return NARROWER
    return None


def _reason(*, chosen: float, base: float, rects_used: int,
            chosen_covered: float, clear: Sequence[float],
            clear_in_spec: Sequence[float]) -> str:
    """Which rule the returned position is consistent with.

    Every test below is on the OUTPUT — the position that came back and what
    the coverage arithmetic says about it. None of them re-runs a branch of
    `resolve_position`, which is the difference between explaining a decision
    and duplicating it.
    """
    if rects_used == 0:
        return NO_RECTS
    if abs(chosen - base) <= 1e-9 and chosen_covered <= 0.0:
        return PRESET_CLEAR
    if not clear:
        return NOTHING_CLEAR
    if chosen_covered <= 0.0:
        if clear_in_spec and SPEC_BAND_LO <= chosen <= SPEC_BAND_HI:
            return BAND_IN_SPEC
        if not clear_in_spec:
            return BAND_OUTSIDE_SPEC
    return UNEXPLAINED


def _usable_size(value: Any) -> bool:
    """Whether an output dimension is one: a finite, positive real number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def _refusal(position: Any, why: list[str]) -> dict:
    """A report that explains nothing, and says which nothing.

    Every field a caller reads is present and empty. A refusal that omits keys
    is a refusal the caller crashes on, which is the failure this exists to
    prevent, one level up.
    """
    return {
        "schema": "caption_choice_v1",
        "scope": "why_the_delivered_caption_sits_where_it_does",
        "position": position if isinstance(position, str) else None,
        "proposed": None, "chosen": None, "moved": None,
        "keep_out": None, "reason": None, "scanned": 0, "clear": 0,
        "bands": [], "rejected": [], "searched": False,
        "chosen_inside_a_clear_run": None, "coverage_at_chosen": None,
        "refused": sorted(set(why)),
        "changes_anything": False,
    }


def explain(position: str, layout: dict | None, *,
            out_w: int = 1080, out_h: int = 1920) -> dict:
    """`caption_choice_v1`: the proposed position, what lost, and why.

    Reads the decision `resolve_position` already made. It does not make one,
    and it does not move anything: the caller gets the same `y_pct` the renderer
    burns, with the search that produced it written down beside it.
    """
    refused: list[str] = []
    if not (_usable_size(out_w) and _usable_size(out_h)):
        refused.append(BAD_OUT_SIZE)
    if layout is not None and not isinstance(layout, dict):
        refused.append(BAD_LAYOUT)
        layout = None
    if position is not None and not isinstance(position, str):
        # `_base_y_pct` calls `.strip()` on it, so a number raises from inside
        # `resolve_position` before this file gets a chance to say anything.
        refused.append(BAD_POSITION)
        position = None
    if refused:
        return _refusal(position, refused)

    out_w, out_h = int(out_w), int(out_h)
    zones = (layout or {}).get("safe_zones")

    # THE ANSWER, from the function that ships it.
    try:
        _x, chosen = resolve_position(position, layout or {},
                                      out_w=out_w, out_h=out_h)
    except Exception:
        # A LAST RESORT, and it is not a substitute for the guards above. Those
        # name what was wrong; this one only says that the shipping function
        # would not answer, which is all that can honestly be said about an
        # input nobody anticipated. Silence here would be worse: the report
        # would describe a placement that never happened.
        return _refusal(position, [UNEXPLAINABLE])
    # THE PRESET, from the same function with nothing to avoid. Not recomputed
    # from `SAFE_TOP` and `CAPTION_BOX_H_PCT`: a second copy of the base rule is
    # a second thing to drift.
    _x, base = resolve_position(position, {}, out_w=out_w, out_h=out_h)
    # THE GRID, from the two functions the search itself calls. Reconstructing
    # it from the rounded return values put a candidate above `hi`.
    lo, hi = scan_bounds(out_h)

    census = _rect_census(zones, out_w, out_h)
    rects = [r for r in (_norm_rect(rc, out_w, out_h)
                         for rc in _iter_rects(zones)) if r is not None]

    out: dict[str, Any] = {
        "schema": "caption_choice_v1",
        "scope": "why_the_delivered_caption_sits_where_it_does",
        "position": str(position or "bottom"),
        "proposed": round(base, 4),
        "chosen": round(chosen, 4),
        "moved": abs(chosen - base) > 1e-9,
        "keep_out": census,
        "refused": [],
        # Never true. This describes the delivered decision; it is not a second
        # opinion about it and nothing downstream may treat it as one.
        "changes_anything": False,
    }
    if not rects:
        # Nothing to avoid, so there is no search to report and no alternative
        # that lost. Saying "zero rejected" out of a run that never scanned
        # would read as "every other position was considered and beaten".
        out.update({"reason": NO_RECTS, "scanned": 0, "clear": 0,
                    "bands": [], "rejected": [], "coverage_at_chosen": 0.0})
        return out

    grid = scan_grid(lo, hi)
    covered = {y: round(_overlap_area(y, rects), 6) for y in grid}
    clear = [y for y in grid if covered[y] <= 0.0]
    bands = _bands(grid, clear)
    in_spec = [b for b in bands if b["in_spec"]]
    clear_in_spec = [y for y in clear if SPEC_BAND_LO <= y <= SPEC_BAND_HI]

    chosen_covered = round(_overlap_area(chosen, rects), 6)
    reason = _reason(chosen=chosen, base=base, rects_used=len(rects),
                     chosen_covered=chosen_covered, clear=clear,
                     clear_in_spec=clear_in_spec)

    # A SEARCH THAT NEVER RAN CHOSE NOTHING AND REJECTED NOTHING.
    # `PRESET_CLEAR` returns before the scan; the other clear runs are real and
    # worth reporting, but calling one of them "taken" would say the delivered
    # position was picked out of them, and calling the rest "rejected" would say
    # they were considered and beaten.
    #
    # AND THE PRESET IS NOT ALWAYS EVEN REACHABLE. `bottom` is 0.75 on a
    # 1920-tall output, which is exactly `hi` — and the grid stops at 0.7442,
    # because 59.58 steps truncate to 59. So the scan can never select the
    # bottom preset position, and a caption left there sits somewhere the search
    # could not have put it. `chosen_inside_a_clear_run` is how the reader finds
    # that out instead of inferring it from an empty `taken`.
    searched = reason in (BAND_IN_SPEC, BAND_OUTSIDE_SPEC, NOTHING_CLEAR,
                          UNEXPLAINED)
    inside = [b for b in bands
              if b["from"] - _EPS <= chosen <= b["to"] + _EPS
              or abs(b["centre"] - chosen) <= _EPS]
    for band in bands:
        # CONTAINMENT, NOT THE CENTRE, when the search ran: `_widest_band`
        # returns a run's midpoint, so matching on the centre works only for
        # runs of odd length and marked every band `taken: false` otherwise —
        # the sidecar then reported that the winner had lost.
        band["taken"] = searched and band in inside
    widest = max((b["steps"] for b in (in_spec or bands)), default=0)
    for band in bands:
        band["rejected_because"] = (
            None if band["taken"] or not searched
            else _why_rejected(band, any_in_spec=bool(in_spec), widest=widest))

    out.update({
        "scanned": len(grid),
        "clear": len(clear),
        "coverage_at_chosen": chosen_covered,
        "bands": bands,
        "searched": searched,
        # Whether the delivered position falls in one of the clear runs at all.
        # It does not have to: the preset branch returns before the scan, and
        # `bottom` sits above the grid's last step by construction.
        "chosen_inside_a_clear_run": bool(inside),
        "rejected": [b for b in bands if not b["taken"]] if searched else [],
        "reason": reason,
    })
    return out
