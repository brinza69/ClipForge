"""What the §4 bands say about a proposal — compared against, never enforced.

Split from `dynamic_rhythm.py` at the repo's 500-line limit, on the seam its
tests already had: that module decides WHEN a cut may exist, this one only
reports how many there turned out to be. Nothing here may add or remove a cut.
A proposal padded to reach a band would make the band unfalsifiable — it would
agree with every implementation, including a wrong one.

THREE THINGS THAT LOOK LIKE BOOKKEEPING AND ARE NOT, all three found by review
rather than by reasoning:

- **A partial motion track is not a partition.** Splitting a clip into "action"
  and "the rest" requires knowing what every second WAS. With a partial series
  the unmeasured seconds fall silently into `quiet`, and a minute nobody
  measured comes back as a confident `below`. Coverage must be complete AND the
  series must have spread, or both partitions are `unavailable`.
- **A cut at an action boundary belongs to neither band.** `action → speaker` is
  a TRANSITION: it happened because the action stopped, and crediting it to the
  quiet band makes the quiet edit look busier than it was for a reason that has
  nothing to do with quiet material.
- **`above` is provable in a window too short to prove `below`.** Three cuts in
  four seconds is 45 a minute against a ceiling of 28, and the clip's length
  does not make that ambiguous. Only the lower bound needs room.
"""

from __future__ import annotations

from typing import Sequence

from services.clipper import edit_profiles
from services.clipper.candidate_terms import _num

__all__ = ["ACTION", "QUIET", "TRANSITION", "VERDICTS",
           "covering", "partition_for", "pace"]

ACTION = "action"
QUIET = "quiet"
#: Credited to neither band, and counted so the three still sum to the total.
TRANSITION = "transition"

VERDICTS: tuple[str, ...] = ("within", "below", "above", "indeterminate",
                             "unavailable", "excluded")

#: Small enough to mean "the instant before", large enough to survive the 3dp
#: rounding every time in this module carries.
_EPS = 1e-4


def covering(segments: Sequence[dict], t: float) -> dict | None:
    """The regime segment containing `t`, or None. Half-open, like the segments."""
    for segment in segments or []:
        if _num(segment.get("t0")) <= t < _num(segment.get("t1")):
            return segment
    return None


def _is_action(segment: dict | None) -> bool:
    return bool(segment) and segment.get("regime") == ACTION


def partition_for(segments: Sequence[dict], t: float) -> str:
    """Which pace a cut at `t` counts towards, from the SEMANTIC moment.

    Read either side of the moment, not just after it. A cut that separates
    action from not-action was earned by the change itself, and putting it in
    the band of whichever stretch happens to follow credits a pace to material
    that did nothing to cause it.
    """
    after = covering(segments, t)
    before = covering(segments, max(0.0, t - _EPS))
    if after is None or before is None:
        # One side outside the measured timeline. Not creditable to a band, and
        # a transition is the honest place for a cut nobody can attribute.
        one = after or before
        return TRANSITION if one is None else (ACTION if _is_action(one) else QUIET)
    loud, was_loud = _is_action(after), _is_action(before)
    if loud != was_loud:
        return TRANSITION
    return ACTION if loud else QUIET


def _seconds_in(segments: Sequence[dict], regime: str, duration: float) -> float:
    total = 0.0
    for segment in segments or []:
        if segment.get("regime") != regime:
            continue
        t0, t1 = _num(segment.get("t0")), min(_num(segment.get("t1")), duration)
        if t1 > t0:
            total += t1 - t0
    return round(total, 3)


def _verdict(cuts: int, seconds: float,
             band: Sequence[float]) -> tuple[str, float | None]:
    """`(verdict, cuts per minute)` against one §4 band.

    ORDER IS LOAD-BEARING. `above` is checked first because it is provable in a
    window too short to prove anything else: three cuts in four seconds is 45 a
    minute whatever the ceiling's sampling needs, and the cuts have already
    happened. Only the LOWER bound needs room — below `60 / lo` seconds the
    band's own floor does not expect a first cut yet, so "no cuts" and "too few
    cuts" are the same observation and `indeterminate` is the honest answer.
    """
    lo, hi = float(band[0]), float(band[1])
    if seconds <= 0:
        return "unavailable", None
    per_min = round(cuts * 60.0 / seconds, 2)
    if per_min > hi:
        return "above", per_min
    if lo > 0 and seconds < 60.0 / lo:
        return "indeterminate", per_min
    if per_min < lo:
        return "below", per_min
    return "within", per_min


def _row(name: str, seconds, cuts, band) -> dict:
    verdict, per_min = _verdict(cuts, seconds, band)
    return {"partition": name, "seconds": seconds, "cuts": cuts,
            "cuts_per_min": per_min, "band": list(band), "verdict": verdict}


def pace(cuts: Sequence[dict], *, duration: float, profile: str,
         regime_segments: Sequence[dict], partition_known: bool,
         unavailable_because: str | None = None) -> list[dict]:
    """The proposal against §4's bands, partition by partition.

    `action` is the one profile with two bands — events set the pace and a lull
    is not an invitation to cut faster — so its clip is split into the seconds
    R3b called `action`, the rest, and the cuts that separate the two. When the
    motion series is missing OR only partial the split cannot be drawn at all,
    and BOTH partitions come back `unavailable` rather than folding the
    unmeasured seconds into `quiet`, where they would read as a measured lull.

    Each cut is attributed by its own SEMANTIC moment — the earliest time a
    reason asked for it — not by where snapping happened to move it.
    """
    times = [float((c.get("requests") or [[None, c["t"]]])[0][1]) for c in cuts or []]
    quiet_band = edit_profiles.band_for(profile, quiet=True)
    band = edit_profiles.band_for(profile)
    if quiet_band is None:
        return [_row("clip", round(duration, 3), len(times), band)]

    if not partition_known:
        return [{"partition": name, "seconds": None, "cuts": None,
                 "cuts_per_min": None, "band": list(b), "verdict": "unavailable",
                 "unavailable_because": unavailable_because or "action_not_measured"}
                for name, b in ((ACTION, band), (QUIET, quiet_band))]

    where = [partition_for(regime_segments, t) for t in times]
    loud = _seconds_in(regime_segments, ACTION, duration)
    return [
        _row(ACTION, loud, where.count(ACTION), band),
        _row(QUIET, round(duration - loud, 3), where.count(QUIET), quiet_band),
        # Not a pace: an instant, credited to neither band, printed so the three
        # counts still add up to the total and nothing goes missing quietly.
        {"partition": TRANSITION, "seconds": None, "cuts": where.count(TRANSITION),
         "cuts_per_min": None, "band": None, "verdict": "excluded",
         "excluded_because": "separates_action_from_not_action"},
    ]
