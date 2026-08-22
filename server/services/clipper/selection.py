"""
ClipForge — AI Stream Clipper: which moments reach the board.

THE DEFECT, measured. `apply_ranking` blends the judge's verdict into `overall`
for the candidates it ranked, and zeroes the ones it declined — but only inside
the pool it was given. Everything outside kept an UNBLENDED heuristic score, so
two incompatible scales competed for the same board.

On the four-hour audited source, after the moment pool and verdict propagation
landed: 7 of the 10 winners were `not_evaluated`. All seven were legacy
candidates from moments that were never in the pool, winning on a raw heuristic
number while every judged candidate had been blended down. A candidate the
judge explicitly declined could lose to one it had never seen.

THE RULE. When a judge ran, the board is drawn from moments it SELECTED. Not
from the whole field ranked by a number that means different things for
different candidates.

    selected                      -> eligible for the board
    not_selected_in_judged_pool   -> the judge saw it and said no
    not_evaluated                 -> the judge never saw it

Two raw scores are never compared. The consequence is stated rather than hidden:
the shortlist becomes the single point of failure for recall, which is why the
budget in candidate_groups spends a share on evidence the heuristic cannot see.

WHEN THERE ARE NOT ENOUGH. A second pool is judged. At most two rounds — a
source with 295 moments would otherwise walk the whole field one pool at a time.
After the cap, the board is filled from `not_evaluated` moments in heuristic
order, and every one of those is MARKED. It never takes from
`not_selected_in_judged_pool`: a moment the judge looked at and refused does not
come back through a side door.

WHEN THE JUDGE FAILS. Nothing here applies. An empty or invalid verdict means
the whole field returns to heuristic order, atomically — a partial application
would leave exactly the mixed scales this module exists to remove.
"""

from __future__ import annotations

from typing import Any, Sequence

SELECTED = "selected"
NOT_SELECTED_IN_JUDGED_POOL = "not_selected_in_judged_pool"
NOT_EVALUATED = "not_evaluated"
STATUSES: tuple[str, ...] = (SELECTED, NOT_SELECTED_IN_JUDGED_POOL, NOT_EVALUATED)

#: Why a board entry is there when the judge did not choose it.
BACKFILL = "heuristic_backfill_after_round_limit"

#: How many pools may be judged for one project. Two is a cost ceiling, not a
#: measured optimum: 80 moments a round, so a 295-moment source stops at 160
#: rather than walking the field. The right number can only be chosen once
#: there is data on how many rounds a long source actually needs.
MAX_POOL_ROUNDS = 2


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def status_of(cand: dict) -> str:
    """What the judge did about this candidate.

    Read from `llm_rank` and `llm_score`, not from the blended `overall`.
    `apply_ranking` gives the field's last place a rank-derived 0.0 and then
    subtracts a penalty per reject reason, so a candidate the judge ranked and
    criticised scores exactly what one it never looked at scores.
    """
    if cand.get("llm_rank") is not None:
        return SELECTED
    if cand.get("llm_score") is not None:
        return NOT_SELECTED_IN_JUDGED_POOL
    return NOT_EVALUATED


def mark(cands: Sequence[dict]) -> dict[str, int]:
    """Stamp `judge_status` on every candidate. Returns the counts."""
    tally: dict[str, int] = {s: 0 for s in STATUSES}
    for cand in cands or ():
        state = status_of(cand)
        cand["judge_status"] = state
        tally[state] += 1
    return tally


def enough_selected(cands: Sequence[dict], want: int) -> bool:
    """Whether another pool round would be worth its cost.

    Counts MOMENTS, not candidates: several cuts of one moment inherit its
    verdict, and a board cannot be filled with four cuts of the same joke.
    """
    moments = {c.get("moment_id") or id(c) for c in (cands or ())
               if status_of(c) == SELECTED}
    return len(moments) >= max(1, int(want))


def board(ranked: Sequence[dict], *, want: int, judged: bool,
          min_score: float = 0.0, revert: bool = False) -> dict:
    """The winners, under the rule. Returns `{winners, backfilled, tally}`.

    `ranked` is the WHOLE deduplicated field, alternatives included, and that
    matters. `deduplicate` elects its group leaders by `overall` — the field
    the judge's verdict was blended into — so a judged candidate, blended DOWN
    from its raw heuristic number, loses its own group to an unjudged sibling
    and is flagged `is_alternative` before this rule ever runs. Measured on the
    four-hour source: of 119 candidates the judge had selected, 3 survived as
    non-alternatives. Filtering on that flag first would have left the rule
    with almost nothing to choose from and quietly backfilled the rest.

    So when a judge ran, this picks from the selected candidates directly, in
    the judge's order, and uses the dedupe grouping only to avoid putting two
    cuts of one moment on the same board.

    With no verdict it is the legacy behaviour exactly, alternatives excluded
    as before: the rule applies to a judged run or not at all.
    """
    ranked = [c for c in (ranked or ()) if isinstance(c, dict)]
    tally = mark(ranked)
    want = max(0, int(want))

    if not judged:
        # `revert` is the judge having FAILED, which is not the same as the
        # rule not applying. A failure means the whole field goes back to the
        # heuristic order, atomically — and by then some candidates have
        # already been blended down by a partial verdict, so ranking on
        # `overall` leaves exactly the mixed scale the failure was supposed to
        # undo. Measured: a candidate with heuristic 80, blended to 30, loses
        # to one with heuristic 60.
        return {"winners": _by_score(
                    [c for c in ranked if not c.get("is_alternative")],
                    want, min_score, heuristic=revert),
                "backfilled": 0, "tally": tally}

    # `(judge_round, llm_rank)`, not `llm_rank` alone. Each round ranks its own
    # pool from 1, so a second round's #1 and a first round's #1 are two
    # different claims — sorting on the number alone interleaves them as though
    # they had been compared, which they never were. Round order is the only
    # thing that relates them: round one saw the better-scoring pool.
    chosen = [c for c in ranked if c.get("judge_status") == SELECTED]
    chosen.sort(key=lambda c: (_num(c.get("judge_round"), 1.0),
                               _num(c.get("llm_rank"), 1e9),
                               -_selection_score(c)))
    winners: list[dict] = []
    seen: set[Any] = set()
    for cand in chosen:
        if len(winners) >= want:
            break
        key = _moment_key(cand)
        if key in seen:
            continue
        seen.add(key)
        winners.append(cand)

    backfilled = 0
    if len(winners) < want:
        # ONLY from moments the judge never saw. A moment it looked at and
        # refused does not come back through a side door.
        spare = [c for c in ranked
                 if c.get("judge_status") == NOT_EVALUATED
                 and not c.get("is_alternative")]
        spare.sort(key=lambda c: -_selection_score(c))
        for cand in spare:
            if len(winners) >= want:
                break
            key = _moment_key(cand)
            if key in seen:
                continue
            seen.add(key)
            cand["board_reason"] = BACKFILL
            winners.append(cand)
            backfilled += 1

    return {"winners": winners, "backfilled": backfilled, "tally": tally}


def rule_applies(mode: str, judged: bool) -> bool:
    """Whether THIS mode lets the rule order the board it ships.

    A function rather than an inline condition because the first version was
    inverted: `judged and not shadow` applied the rule in story_v1 and
    llm_nominate, modes that never asked for it, and skipped it in shadow, the
    one mode written for it. An inline boolean could only be tested by reading
    the source; this can be tested by asking it.
    """
    return bool(judged) and str(mode) == "story_v2"


def apply_board(ranked: Sequence[dict], winners: Sequence[dict]) -> None:
    """COMMIT a board: number its winners and flag everything else.

    Separate from `board()` on purpose. `board()` decides and returns; this
    writes. Shadow mode calls `board()` a second time to record what v2 WOULD
    have chosen, and while the numbering lived inside it that second call
    stamped `rank_position` onto candidates the shipped board had not selected
    — the comparison corrupting the very thing it was supposed to leave alone.

    Everything dedupe kept but the board did not take is an ALTERNATIVE —
    without that a 6-hour VOD showed 352 "winners" for a request of 8. The half
    that was missing is the CLEARING: `deduplicate` may have flagged a winner as
    an alternative before the rule ran, and leaving the flag on meant the
    rescued winner was stored as an alternative and never reached the board.
    """
    chosen = {id(c) for c in winners}
    for cand in ranked or ():
        if not isinstance(cand, dict):
            continue
        cand["is_alternative"] = id(cand) not in chosen
        if id(cand) not in chosen:
            cand["rank_position"] = None
    # `deduplicate` numbers the winners IT elected and gives everything else 0,
    # and this rule elects a different set — so without its own numbering a
    # rescued winner reached the board carrying `rank_position=0` and the v2
    # order never arrived in the DB, the API or auto-export.
    for position, cand in enumerate(winners, start=1):
        if isinstance(cand, dict):
            cand["rank_position"] = position


def _selection_score(cand: dict) -> float:
    """The number THIS rule ranks on, which is never the legacy one.

    `selection_score` when the verdict propagation wrote one, `overall`
    otherwise. Keeping them apart is what lets shadow compute a v2 board
    without moving the legacy board it is being compared against.
    """
    value = cand.get("selection_score")
    return _num(value if value is not None else cand.get("overall"))


def _moment_key(cand: dict) -> Any:
    """What makes two candidates the same clip for board purposes.

    `moment_id` when the grouping gave one, `dedupe_group` otherwise, and the
    object itself when neither exists — never a shared default, which would
    collapse an entire legacy board into one entry.
    """
    return cand.get("moment_id") or cand.get("dedupe_group") or id(cand)


def _by_score(ranked: Sequence[dict], want: int, min_score: float, *,
              heuristic: bool = False) -> list[dict]:
    """The legacy pick: best by `overall`, with the min-score floor.

    `heuristic=True` ranks on `heuristic_score` instead — the reading no
    verdict ever touched. Used only when a judge FAILED, where the plan asks
    for the whole field to return to the heuristic order atomically.

    Never returns an empty board because the threshold was set too high — keep
    the best one and let the score speak for itself.
    """
    def key(cand: dict) -> float:
        if heuristic and cand.get("heuristic_score") is not None:
            return _num(cand["heuristic_score"])
        return _num(cand.get("overall"))

    out = sorted(ranked, key=lambda c: -key(c))
    if min_score > 0:
        held = [c for c in out if key(c) >= min_score]
        out = held or out[:1]
    return out[:want]
